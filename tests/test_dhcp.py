import socket
from unittest.mock import Mock

import pytest

from spark_recovery.dhcp import BROADCAST, COOKIE, bind_interface, option, parse_options, response


def request(session, message=1, *, pxe=True, arch=b"\0\x0b", server=None):
    packet = bytearray(240)
    packet[:4] = b"\x01\x01\x06\0"
    packet[4:8] = b"TEST"
    packet[28:34] = bytes.fromhex(session.target_mac.replace(":", ""))
    packet[236:240] = COOKIE
    packet += option(53, bytes([message]))
    if pxe:
        packet += option(60, b"PXEClient:Arch:00011:UNDI:003016") + option(93, arch)
    if message == 3:
        packet += option(50, socket.inet_aton(session.client))
    if server:
        packet += option(54, socket.inet_aton(server))
    return packet + b"\xff"


@pytest.mark.parametrize("pxe", [True, False])
@pytest.mark.parametrize("message,expected", [(1, 2), (3, 5)])
def test_firmware_and_kernel_dora(session, pxe, message, expected):
    result = response(request(session, message, pxe=pxe), session)
    opts = parse_options(result)
    assert opts[53] == bytes([expected])
    assert result[16:20] == socket.inet_aton(session.client)
    assert (67 in opts) == pxe
    assert BROADCAST == "255.255.255.255"


def test_other_client_arch_server_ignored(session):
    packet = request(session)
    packet[28] ^= 2
    assert response(packet, session) is None
    assert response(request(session, arch=b"\0\x07"), session) is None
    assert response(request(session, arch=b"\0"), session) is None
    assert response(request(session, message=3, server="172.31.250.9"), session) is None
    assert response(b"malformed", session) is None
    assert parse_options(b"\0" * 240 + b"\x35\x08bad") == {}


def test_platform_interface_binding():
    sock = Mock()
    bind_interface(sock, "12", "win32")
    sock.setsockopt.assert_called_with(socket.IPPROTO_IP, 31, b"\0\0\0\x0c")
    bind_interface(sock, "12", "darwin")
    sock.setsockopt.assert_called_with(socket.IPPROTO_IP, 25, 12)
