"""Single-target DHCP: firmware ARM64 PXE plus subsequent recovery-kernel leases."""
from __future__ import annotations

import ipaddress
import logging
import socket
import struct
import sys
import threading

from .config import Session

COOKIE = bytes.fromhex("63825363")
BOOTFILE = b"grubnetaa64.efi.signed"
BROADCAST = "255.255.255.255"


def parse_options(packet: bytes) -> dict[int, bytes]:
    result = {}
    offset = 240
    while offset < len(packet):
        tag = packet[offset]
        offset += 1
        if tag == 255:
            return result
        if tag == 0:
            continue
        if offset >= len(packet):
            return {}
        size = packet[offset]
        offset += 1
        if offset + size > len(packet):
            return {}
        result[tag] = packet[offset:offset + size]
        offset += size
    return result


def option(tag: int, value: bytes) -> bytes:
    return bytes((tag, len(value))) + value


def response(packet: bytes, config: Session) -> bytes | None:
    if (len(packet) < 240 or packet[:3] != b"\x01\x01\x06" or packet[236:240] != COOKIE
            or packet[24:28] != b"\0" * 4
            or packet[28:34] != bytes.fromhex(config.target_mac.replace(":", ""))):
        return None
    opts = parse_options(packet)
    kind = opts.get(53)
    if kind not in (b"\x01", b"\x03"):
        return None
    pxe = opts.get(60, b"").startswith(b"PXEClient")
    architecture = opts.get(93, b"")
    if ((pxe or architecture) and (len(architecture) % 2 or b"\0\x0b" not in
                                   [architecture[i:i + 2] for i in range(0, len(architecture), 2)])):
        return None
    if 54 in opts and opts[54] != socket.inet_aton(config.server):
        return None
    if 50 in opts and opts[50] != socket.inet_aton(config.client):
        return None
    if packet[12:16] not in (b"\0" * 4, socket.inet_aton(config.client)):
        return None
    reply = bytearray(236)
    reply[:4] = b"\x02\x01\x06\0"
    reply[4:8] = packet[4:8]
    reply[10:12] = b"\x80\0"
    reply[16:20] = socket.inet_aton(config.client)
    reply[20:24] = socket.inet_aton(config.server)
    reply[28:44] = packet[28:44]
    if pxe:
        reply[108:108 + len(BOOTFILE)] = BOOTFILE
    netmask = str(ipaddress.IPv4Network(f"{config.server}/{config.prefix}", strict=False).netmask)
    reply += COOKIE + option(53, b"\x02" if kind == b"\x01" else b"\x05")
    reply += option(54, socket.inet_aton(config.server)) + option(51, struct.pack("!I", 3600))
    reply += option(1, socket.inet_aton(netmask))
    if pxe:
        reply += option(66, config.server.encode()) + option(67, BOOTFILE + b"\0")
    if 61 in opts:
        reply += option(61, opts[61])
    return bytes(reply + b"\xff").ljust(300, b"\0")


def bind_interface(sock: socket.socket, interface: str, platform: str | None = None) -> None:
    platform = platform or sys.platform
    index = int(interface) if interface.isdecimal() else socket.if_nametoindex(interface)
    if platform == "win32":
        sock.setsockopt(socket.IPPROTO_IP, 31, struct.pack("!I", index))  # IP_UNICAST_IF
    elif platform == "darwin":
        sock.setsockopt(socket.IPPROTO_IP, 25, index)  # IP_BOUND_IF, native unsigned int
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode() + b"\0")


class DHCPServer:
    def __init__(self, config: Session):
        self.config = config
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if sys.platform == "win32":
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            bind_interface(self.sock, config.interface)
            self.sock.bind(("0.0.0.0", 67))  # noqa: S104 -- receives initial broadcasts; target MAC filtered
            self.sock.settimeout(0.5)
        except BaseException:
            self.sock.close()
            raise

    def serve(self, stop: threading.Event):
        while not stop.is_set():
            try:
                packet, _ = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            reply = response(packet, self.config)
            if reply:
                self.sock.sendto(reply, (BROADCAST, 68))
                logging.info("DHCP %s -> target %s, xid=%s", parse_options(reply)[53].hex(),
                             self.config.target_mac, packet[4:8].hex())

    def close(self):
        self.sock.close()

