"""Read-only TFTP, option negotiation, bounded retries, and 16-bit block rollover.

No POSIX file-lock dependencies. One target, bounded concurrent transfers;
uploads and paths outside the boot root are always rejected.
"""
from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath
import socket
import struct
import threading
import time


def safe_path(root: Path, name: str) -> Path:
    if not name or "\\" in name or ":" in name or "\0" in name:
        raise ValueError("Invalid TFTP path")
    relative = PurePosixPath(name.lstrip("/"))
    if ".." in relative.parts:
        raise ValueError("Path outside TFTP root")
    result = (root / str(relative)).resolve()
    if not result.is_relative_to(root.resolve()) or not result.is_file():
        raise ValueError("File not available")
    return result


def parse_rrq(packet: bytes) -> tuple[str, dict[str, str]]:
    if len(packet) < 4 or packet[:2] != b"\0\x01" or not packet.endswith(b"\0"):
        raise ValueError("Only RRQ supported")
    fields = packet[2:-1].decode("ascii").split("\0")
    if len(fields) < 2 or fields[1].lower() != "octet" or (len(fields) - 2) % 2:
        raise ValueError("Malformed RRQ or non-octet mode")
    return fields[0], {fields[i].lower(): fields[i + 1] for i in range(2, len(fields), 2)}


class TFTPServer:
    def __init__(self, root: Path, host: str, allowed: set[str], port: int = 69,
                 timeout: float = 2, retries: int = 5):
        self.root, self.host, self.allowed = root.resolve(), host, allowed
        self.timeout, self.retries = timeout, retries
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind((host, port))
            self.sock.settimeout(0.5)
        except BaseException:
            self.sock.close()
            raise
        self.port = self.sock.getsockname()[1]
        self.lock = threading.Lock()
        self.sessions: dict[tuple, socket.socket | None] = {}
        self.stopping = threading.Event()

    @staticmethod
    def error(sock: socket.socket, peer: tuple, code: int, text: str):
        sock.sendto(struct.pack("!HH", 5, code) + text.encode("ascii") + b"\0", peer)

    def serve(self, stop: threading.Event):
        while not stop.is_set() and not self.stopping.is_set():
            try:
                packet, peer = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            if peer[0] not in self.allowed:
                continue
            if packet[:2] == b"\0\x02":
                self.error(self.sock, peer, 2, "Uploads disabled")
                continue
            try:
                name, options = parse_rrq(packet)
                path = safe_path(self.root, name)
                blocksize = int(options.get("blksize", "512"))
                if not 8 <= blocksize <= 65464:
                    raise ValueError("Invalid block size")
            except (ValueError, UnicodeError):
                self.error(self.sock, peer, 2, "Invalid path or request")
                continue
            with self.lock:
                if peer in self.sessions or len(self.sessions) >= 4:
                    continue  # active session retransmits its own pending packet
                self.sessions[peer] = None
            threading.Thread(target=self.transfer, args=(peer, path, options, blocksize), daemon=True).start()

    def transfer(self, peer: tuple, path: Path, options: dict[str, str], blocksize: int):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self.host, 0))  # replies stay on the explicit host interface
            with self.lock:
                self.sessions[peer] = sock
            accepted = {}
            if "blksize" in options:
                blocksize = min(blocksize, 1468)  # avoid fragmented UDP on Ethernet
                accepted["blksize"] = str(blocksize)
            if "tsize" in options:
                if options["tsize"] != "0":
                    raise ValueError("Invalid tsize request")
                accepted["tsize"] = str(path.stat().st_size)
            if accepted:
                oack = b"\0\x06" + b"".join(k.encode() + b"\0" + v.encode() + b"\0"
                                             for k, v in accepted.items())
                if not self.send_and_ack(sock, peer, oack, 0):
                    return
            block = 1
            with path.open("rb") as source:
                while not self.stopping.is_set():
                    data = source.read(blocksize)
                    if not self.send_and_ack(sock, peer, struct.pack("!HH", 3, block) + data, block):
                        return
                    if len(data) < blocksize:
                        logging.info("TFTP completed %s -> %s (%s bytes)", path.name, peer[0], source.tell())
                        return
                    block = (block + 1) & 65535
        except (OSError, ValueError) as error:
            logging.warning("TFTP session %s failed: %s", peer, error)
        finally:
            sock.close()
            with self.lock:
                self.sessions.pop(peer, None)

    def send_and_ack(self, sock: socket.socket, peer: tuple, data: bytes, block: int) -> bool:
        for _ in range(self.retries):
            if self.stopping.is_set():
                return False
            sock.sendto(data, peer)
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                sock.settimeout(max(0.001, deadline - time.monotonic()))
                try:
                    received, sender = sock.recvfrom(2048)
                except socket.timeout:
                    break
                if sender != peer:
                    continue
                if received == struct.pack("!HH", 4, block):
                    return True
                if received[:2] == b"\0\x05":
                    return False
        logging.warning("TFTP timed out at block %s for %s", block, peer)
        return False

    def close(self):
        self.stopping.set()
        self.sock.close()
        with self.lock:
            sockets = [item for item in self.sessions.values() if item is not None]
        for item in sockets:
            item.close()
