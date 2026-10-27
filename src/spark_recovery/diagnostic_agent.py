"""Optional read-only RAM diagnostic observer for the pinned ASUS image.

No erase, shell command endpoint, credentials, firmware writes or reboot logic.
The installation entry always uses the untouched original initrd.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import secrets
import shutil
import stat
import time

from .config import RecoveryError, Session, atomic_json, digest
from .safety import image_manifest, write_menu
from .server import HTTPHandler


def records(blob: bytes):
    position = 0
    for _ in range(100000):
        if blob[position:position + 6] not in (b"070701", b"070702"):
            return
        fields = [int(blob[position + offset:position + offset + 8], 16) for offset in range(6, 110, 8)]
        size, namesize = fields[6], fields[11]
        if not 1 <= namesize <= 4096:
            raise RecoveryError("Invalid initrd path size")
        start = position + 110
        content = (start + namesize + 3) & ~3
        end = (content + size + 3) & ~3
        if end > len(blob):
            raise RecoveryError("Truncated initrd")
        name = blob[start:start + namesize - 1].decode("utf-8")
        if name == "TRAILER!!!":
            return
        yield name, fields[1], blob[content:content + size]
        position = end
    raise RecoveryError("Too many initrd files")


def read_initrd_files(path: Path, wanted: set[str]) -> dict:
    import zstandard
    data = path.read_bytes()
    result = {}
    position = 0
    while position < len(data):
        if data[position] == 0:
            position += 1
            continue
        if data[position:position + 6] in (b"070701", b"070702"):
            # Locate the end of an early, uncompressed cpio segment.
            end = position
            while data[end:end + 6] in (b"070701", b"070702"):
                size = int(data[end + 54:end + 62], 16)
                namesize = int(data[end + 94:end + 102], 16)
                content = (end + 110 + namesize + 3) & ~3
                next_position = (content + size + 3) & ~3
                if next_position <= end or next_position > len(data):
                    raise RecoveryError("Invalid early initrd segment")
                name = data[end + 110:end + 110 + namesize - 1]
                end = next_position
                if name == b"TRAILER!!!":
                    break
            segment = data[position:end]
            position = end
        else:
            if data[position:position + 4] == b"\x28\xb5\x2f\xfd":
                stream = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data[position:]))
            elif data[position:position + 2] == b"\x1f\x8b":
                stream = gzip.GzipFile(fileobj=io.BytesIO(data[position:]))
            else:
                raise RecoveryError("Unsupported diagnostic initrd compression")
            with stream:
                segment = stream.read(1024**3 + 1)
            if len(segment) > 1024**3:
                raise RecoveryError("Initrd exceeds diagnostic inspection limit")
            position = len(data)
        for name, _mode, content in records(segment):
            if name in wanted:
                result[name] = content
    return result


def cpio_entry(name: str, data: bytes, mode=stat.S_IFREG | 0o755) -> bytes:
    encoded = name.encode() + b"\0"
    fields = [1, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(encoded), 0]
    result = b"070701" + b"".join(f"{value:08x}".encode() for value in fields) + encoded
    result += b"\0" * (-len(result) % 4)
    result += data
    return result + b"\0" * (-len(result) % 4)


def agent_script(session: Session, token: str):
    # Only validated Session literals and a generated hex token enter this script.
    if len(token) != 64 or any(value not in "0123456789abcdef" for value in token):
        raise RecoveryError("Invalid diagnostic token")
    return f'''#!/bin/sh
case "$(cat /proc/cmdline)" in *mkd.diagnostic=1*) ;; *) exit 0 ;; esac
case "$(cat /proc/cmdline)" in *autoinstall*) exit 0 ;; esac
case "$1" in prereqs) exit 0 ;; esac
(
  count=0
  while [ "$count" -lt 900 ]; do
    count=$((count + 1))
    [ -f /cdrom/oemdata/oem-iso-cfg.sh ] && break
    sleep 1
  done
  ro=0; oem=0; hidden=0; matched=0
  awk '$2=="/cdrom" && $3=="iso9660" && $4 ~ /(^|,)ro(,|$)/ {{ok=1}} END {{exit !ok}}' /proc/mounts && ro=1
  [ -f /cdrom/oemdata/oem-iso-cfg.sh ] && [ -d /cdrom/oemdata/debs ] && oem=1
  [ -z "$(ls /dev/nvme* 2>/dev/null)" ] && hidden=1
  for address in /sys/class/net/*/address; do
    [ "$(cat "$address" 2>/dev/null)" = "{session.target_mac}" ] && matched=1
  done
  tries=0
  while [ "$tries" -lt 12 ]; do
    wget -q -T 10 -O /run/mkd-diagnostic-response \
      "http://{session.server}:{session.http_port}/_mkd/diagnostic/{token}/report/$ro$oem$hidden$matched" && break
    tries=$((tries + 1)); sleep 3
  done
) >/run/mkd-diagnostic.log 2>&1 &
exit 0
'''.encode()


def configure(root: Path, session: Session):
    image = image_manifest(root, session)
    if image.get("diagnostic_agent"):
        raise RecoveryError("Diagnostic observer already configured; prepare a fresh boot tree first")
    source = root / "tftp/casper/initrd"
    files = read_initrd_files(source, {"scripts/init-premount/ORDER", "scripts/casper", "usr/bin/wget"})
    order = files.get("scripts/init-premount/ORDER")
    casper = files.get("scripts/casper", b"")
    if not order or b"maybe_break casper-bottom" not in casper or not files.get("usr/bin/wget"):
        raise RecoveryError("Pinned initrd lacks the tested hook/wget capability; automatic checks are unavailable")
    token = secrets.token_hex(32)
    hook = b"/scripts/init-premount/mkd-diagnostic \"$@\"\n"
    archive = cpio_entry("scripts/init-premount/ORDER", order + b"\n" + hook)
    archive += cpio_entry("scripts/init-premount/mkd-diagnostic", agent_script(session, token))
    archive += cpio_entry("TRAILER!!!", b"", 0)
    destination = root / "tftp/casper/initrd-diagnostic"
    with source.open("rb") as original, destination.open("wb") as output:
        shutil.copyfileobj(original, output)
        output.write(b"\0" * (-output.tell() % 4))
        output.write(archive)
    descriptor = {"session": session.fingerprint, "token": token, "created_at": time.time()}
    atomic_json(root / "diagnostic-agent.json", descriptor)
    image["diagnostic_agent"] = {"session": session.fingerprint, "token_sha256": hashlib.sha256(token.encode()).hexdigest()}
    image["boot_files"]["casper/initrd-diagnostic"] = digest(destination)
    atomic_json(root / "image.json", image)
    write_menu(root, session)


def receive(root: Path, session: Session, token: str, body: dict):
    descriptor = json.loads((root / "diagnostic-agent.json").read_text(encoding="utf-8"))
    image = image_manifest(root, session)
    expected = descriptor.get("token", "")
    if (not isinstance(token, str) or not secrets.compare_digest(token, expected)
            or descriptor.get("session") != session.fingerprint
            or time.time() - descriptor.get("created_at", 0) > 24 * 3600
            or hashlib.sha256(token.encode()).hexdigest() != image.get("diagnostic_agent", {}).get("token_sha256")):
        raise RecoveryError("Diagnostic report is stale or not bound to this session")
    guards = ("iso_read_only", "oem_present", "nvme_hidden", "mac_matched")
    if not isinstance(body, dict) or set(body) != set(guards) or any(type(body[key]) is not bool for key in guards):
        raise RecoveryError("Invalid diagnostic result")
    atomic_json(root / "diagnostic-agent-result.json", {"session": session.fingerprint,
                "checks": body, "at": time.time(), "evidence": "temporary_ram_observer",
                "token_sha256": image["diagnostic_agent"]["token_sha256"], "image": image["iso_sha256"]})
    if not all(body.values()):
        (root / "diagnostic.json").unlink(missing_ok=True)
        (root / "install.json").unlink(missing_ok=True)
        write_menu(root, session)
        raise RecoveryError("Target diagnostic checks failed; installation remains blocked")
    atomic_json(root / "diagnostic.json", {"session": session.fingerprint, "image": image["iso_sha256"],
        "operator_attestation": False, "agent_verified": True,
        "agent_token_sha256": image["diagnostic_agent"]["token_sha256"],
        "iso_read_only": True, "oem_present": True, "nvme_hidden": True, "at": time.time()})


class DiagnosticHTTPHandler(HTTPHandler):
    def __init__(self, *args, root: Path, session: Session, **kwargs):
        self.root, self.session = root, session
        super().__init__(*args, **kwargs)

    def do_GET(self):
        # Ubuntu's minimal initramfs wget supports GET but omits POST. This narrow
        # endpoint accepts four bits of read-only observations, never commands.
        if not self.path.startswith("/_mkd/"):
            super().do_GET()
            return
        match = re.fullmatch(r"/_mkd/diagnostic/([a-f0-9]{64})/report/([01]{4})", self.path)
        if self.client_address[0] != self.session.client or match is None:
            self.send_error(403)
            return
        body = dict(zip(("iso_read_only", "oem_present", "nvme_hidden", "mac_matched"),
                        (value == "1" for value in match[2]), strict=True))
        try:
            receive(self.root, self.session, match[1], body)
        except (OSError, ValueError, RecoveryError, KeyError, TypeError):
            self.send_error(400, "Diagnostic result refused")
            return
        self.send_response(200)
        self.send_header("Content-Length", "3")
        self.end_headers()
        self.wfile.write(b"ok\n")

    def do_POST(self):
        prefix = "/_mkd/diagnostic/"
        if self.client_address[0] != self.session.client or not self.path.startswith(prefix):
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2048:
                raise RecoveryError("Invalid diagnostic body size")
            self.connection.settimeout(5)
            body = json.loads(self.rfile.read(length))
            receive(self.root, self.session, self.path[len(prefix):], body)
        except (OSError, ValueError, RecoveryError, KeyError, TypeError):
            self.send_error(400, "Diagnostic result refused")
            return
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, template, *args):
        if not self.path.startswith("/_mkd/"):
            super().log_message(template, *args)
