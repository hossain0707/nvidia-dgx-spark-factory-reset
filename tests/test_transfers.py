from functools import partial
from http.server import ThreadingHTTPServer
import socket
import struct
import threading
import urllib.error
import urllib.request

import pytest

from spark_recovery.server import HTTPHandler
from spark_recovery.tftp import TFTPServer, parse_rrq, safe_path


def download(host, port, name, size=1468, *, lose_ack=False, duplicate=False):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(5)
        rrq = b"\0\1" + name.encode() + b"\0octet\0blksize\0" + str(size).encode() + b"\0tsize\x000\0"
        sock.sendto(rrq, (host, port))
        data, peer = sock.recvfrom(65535)
        assert data[:2] == b"\0\6"
        fields = data[2:-1].split(b"\0")
        options = dict(zip(fields[::2], fields[1::2], strict=True))
        size = int(options[b"blksize"])
        sock.sendto(b"\0\4\0\0", peer)
        result, previous, skipped = bytearray(), 0, False
        while True:
            data, sender = sock.recvfrom(65535)
            assert sender == peer and data[:2] == b"\0\3"
            number = struct.unpack("!H", data[2:4])[0]
            if lose_ack and not skipped:
                skipped = True
                continue
            if number == ((previous + 1) & 65535):
                result.extend(data[4:])
                previous = number
            sock.sendto(b"\0\4" + data[2:4], peer)
            if duplicate:
                sock.sendto(b"\0\4" + data[2:4], peer)
            if len(data) - 4 < size:
                assert len(result) == int(options[b"tsize"])
                return bytes(result)


@pytest.fixture
def tftp(tmp_path):
    stop = threading.Event()
    server = TFTPServer(tmp_path, "127.0.0.1", {"127.0.0.1"}, port=0, timeout=0.1, retries=8)
    thread = threading.Thread(target=server.serve, args=(stop,))
    thread.start()
    yield server, tmp_path
    stop.set()
    thread.join(timeout=3)
    server.close()


@pytest.mark.parametrize("size", [0, 1, 1468, 1469, 2936, 100000])
def test_file_sizes(tftp, size):
    server, root = tftp
    data = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
    (root / "file").write_bytes(data)
    assert download("127.0.0.1", server.port, "/file") == data


def test_rollover_and_consecutive_sessions(tftp):
    server, root = tftp
    data = b"a" * (8 * 65536 + 1)
    (root / "rollover").write_bytes(data)
    assert download("127.0.0.1", server.port, "rollover", size=8) == data
    assert download("127.0.0.1", server.port, "rollover", size=65464) == data


def test_loss_and_duplicate_acks(tftp):
    server, root = tftp
    (root / "file").write_bytes(b"DATA" * 1000)
    assert download("127.0.0.1", server.port, "file", lose_ack=True, duplicate=True) == b"DATA" * 1000


def test_upload_and_traversal_denied(tftp):
    server, root = tftp
    for packet in (b"\0\2write\0octet\0", b"\0\1../secret\0octet\0", b"\0\1C:\\secret\0octet\0"):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2)
            sock.sendto(packet, ("127.0.0.1", server.port))
            data, _ = sock.recvfrom(2048)
            assert data[:4] == b"\0\5\0\2"
    assert not (root / "write").exists()


@pytest.mark.parametrize("name", ["../x", "/../x", "a/../../x", "C:/x", "\\x", "", "missing"])
def test_bad_paths(tmp_path, name):
    with pytest.raises(ValueError):
        safe_path(tmp_path, name)


def test_symlink_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    secret = tmp_path / "secret"
    secret.write_bytes(b"private")
    try:
        (root / "link").symlink_to(secret)
    except OSError:
        pytest.skip("Symlink creation needs a Windows privilege not granted on this runner")
    with pytest.raises(ValueError):
        safe_path(root, "link")


@pytest.mark.parametrize("packet", [b"", b"\0\1x\0netascii\0", b"\0\1x\0octet\0key\0", b"\0\1x\0octet"])
def test_invalid_rrq(packet):
    with pytest.raises(ValueError):
        parse_rrq(packet)


def test_http_allowlist_and_readonly(tmp_path):
    iso = tmp_path / "image"
    iso.write_bytes(b"DATA")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(HTTPHandler,
        files={"/recovery.iso": iso}, allowed={"127.0.0.1"}))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url + "/recovery.iso", timeout=3) as response:
            assert response.read() == b"DATA"
            assert response.headers["Content-Length"] == "4"
        for path in ("/", "/../session.json", "/session.json", "/recovery.iso/../image"):
            with pytest.raises(urllib.error.HTTPError) as error:
                opener.open(url + path, timeout=3)
            assert error.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as error:
            opener.open(urllib.request.Request(url + "/recovery.iso", data=b"BAD", method="POST"), timeout=3)  # noqa: S310 -- loopback-only fixture
        assert error.value.code == 501
        assert iso.read_bytes() == b"DATA"
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
