"""Verify and extract the pinned ASUS image without mounting physical disks."""
from __future__ import annotations

import gzip
import io
import logging
from pathlib import Path
import shutil
import struct
import tarfile
import urllib.request
import zipfile

from .config import RecoveryError, Session, atomic_json, digest
from .menu import render
from .profiles import ASUS


def download(url: str, destination: Path, expected: str) -> Path:
    if not url.startswith("https://"):
        raise RecoveryError("Artifact downloads require HTTPS")
    if destination.exists() and digest(destination) == expected:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    logging.info("Downloading official artifact: %s", url)
    # Retrying a partial artifact starts over; never append unverified bytes.
    with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as output:  # noqa: S310
        if not response.geturl().startswith("https://"):
            raise RecoveryError("Refusing a redirect to an insecure artifact URL")
        shutil.copyfileobj(response, output, 4 * 1024 * 1024)
    if digest(partial) != expected:
        raise RecoveryError(f"Artifact SHA256 mismatch; not activated. Inspect {partial}")
    partial.replace(destination)
    return destination


def extract_package(package: Path, destination: Path) -> None:
    if digest(package) != ASUS["package_sha256"]:
        raise RecoveryError("Package does not match the approved ASUS profile SHA256")
    with zipfile.ZipFile(package) as outer:
        entries = [item for item in outer.infolist() if item.filename.endswith(".tar.gz")]
        if len(entries) != 1:
            raise RecoveryError("Unsupported package layout")
        with outer.open(entries[0]) as source, tarfile.open(fileobj=source, mode="r|gz") as inner:
            for member in inner:
                if member.isfile() and member.name.endswith(".iso"):
                    if member.size != ASUS["iso_bytes"]:
                        raise RecoveryError("Unexpected ISO length")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    partial = destination.with_suffix(".iso.partial")
                    stream = inner.extractfile(member)
                    if stream is None:
                        raise RecoveryError("Cannot read ISO payload")
                    with stream, partial.open("wb") as output:
                        shutil.copyfileobj(stream, output, 4 * 1024 * 1024)
                    if digest(partial) != ASUS["iso_sha256"]:
                        raise RecoveryError("Extracted ISO SHA256 mismatch")
                    partial.replace(destination)
                    return
    raise RecoveryError("No approved ISO in package")


def inspect_initrd(path: Path) -> dict:
    import zstandard
    data = path.read_bytes()
    limit = 1024 * 1024 * 1024
    names = set()
    casper = b""

    def cpio(blob):
        nonlocal casper
        position = 0
        for _ in range(100000):
            if blob[position:position + 6] not in (b"070701", b"070702"):
                return position
            header = blob[position:position + 110]
            if len(header) != 110:
                raise RecoveryError("Truncated initrd header")
            fields = [int(header[i:i + 8], 16) for i in range(6, 110, 8)]
            size, name_size = fields[6], fields[11]
            if not 1 <= name_size <= 4096:
                raise RecoveryError("Invalid initrd name length")
            start = position + 110
            name = blob[start:start + name_size - 1].decode("utf-8", errors="replace")
            content = (start + name_size + 3) & ~3
            position = (content + size + 3) & ~3
            if position > len(blob):
                raise RecoveryError("Truncated initrd record")
            if name == "TRAILER!!!":
                return position
            names.add(name)
            if name == "scripts/casper" and size < 2 * 1024 * 1024:
                casper = blob[content:content + size]
        raise RecoveryError("Too many initrd records")

    position = 0
    try:
        while position < len(data):
            if data[position] == 0:
                position += 1
            elif data[position:position + 6] in (b"070701", b"070702"):
                consumed = cpio(data[position:])
                if not consumed:
                    raise RecoveryError("Invalid initrd segment")
                position += consumed
            else:
                if data[position:position + 4] == b"\x28\xb5\x2f\xfd":
                    stream = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data[position:]))
                elif data[position:position + 2] == b"\x1f\x8b":
                    stream = gzip.GzipFile(fileobj=io.BytesIO(data[position:]))
                else:
                    raise RecoveryError("Unsupported initrd compression")
                with stream:
                    decoded = stream.read(limit + 1)
                if len(decoded) > limit:
                    raise RecoveryError("Initrd decompression exceeds 1 GiB limit")
                cpio(decoded)
                break
    except (ValueError, OSError) as error:
        raise RecoveryError(f"Invalid initrd: {error}") from error
    guards = {
        "http_iso_loading": b"iso-url=*.iso" in casper and b"do_urlmount" in casper,
        "diagnostic_break": b"maybe_break casper-bottom" in casper,
        "ethernet_r8127": any(name.endswith("/r8127.ko.zst") for name in names),
        "nvme_modular": any(name.endswith("/nvme.ko.zst") for name in names),
    }
    if not all(guards.values()):
        raise RecoveryError(f"Recovery image fails required diagnostic guards: {guards}")
    return guards


def verify_bootloader(path: Path) -> None:
    if digest(path) != ASUS["bootloader_sha256"]:
        raise RecoveryError("Bootloader SHA256 changed; maintainer review required")
    data = path.read_bytes()
    if len(data) < 64 or data[:2] != b"MZ":
        raise RecoveryError("Invalid EFI executable")
    offset = struct.unpack_from("<I", data, 60)[0]
    if (offset + 6 > len(data) or data[offset:offset + 4] != b"PE\0\0"
            or struct.unpack_from("<H", data, offset + 4)[0] != 0xAA64):
        raise RecoveryError("Bootloader is not ARM64 PE/COFF")


def prepare(root: Path, session: Session, iso: Path | None = None, package: Path | None = None,
            bootloader: Path | None = None) -> dict:
    import pycdlib
    root.mkdir(parents=True, exist_ok=True)
    if iso is None:
        assets = root / "assets"
        assets.mkdir(exist_ok=True)
        if package is None:
            if shutil.disk_usage(root).free < 24 * 1024**3:
                raise RecoveryError("Allow at least 24 GiB free to download and unpack the ASUS package")
            package = download(ASUS["url"], assets / "asus-package.zip", ASUS["package_sha256"])
        iso = assets / "recovery.iso"
        if not iso.exists() or digest(iso) != ASUS["iso_sha256"]:
            extract_package(package, iso)
    iso = iso.resolve()
    if iso.stat().st_size != ASUS["iso_bytes"] or digest(iso) != ASUS["iso_sha256"]:
        raise RecoveryError("ISO is not the pinned ASUS GX10 image; refusing to prepare it")
    tftp = root / "tftp"
    (tftp / "casper").mkdir(parents=True, exist_ok=True)
    (tftp / "grub").mkdir(exist_ok=True)
    image = pycdlib.PyCdlib()
    image.open(str(iso))
    try:
        for name in ("vmlinuz", "initrd"):
            image.get_file_from_iso(str(tftp / "casper" / name), rr_path="/casper/" + name)
        image.get_record(rr_path="/oemdata/oem-iso-cfg.sh")
        image.get_record(rr_path="/oemdata/debs")
    finally:
        image.close()
    guards = inspect_initrd(tftp / "casper" / "initrd")
    destination = tftp / "grubnetaa64.efi.signed"
    if bootloader:
        verify_bootloader(bootloader)
        shutil.copyfile(bootloader, destination)
    else:
        download(ASUS["bootloader_url"], destination, ASUS["bootloader_sha256"])
        verify_bootloader(destination)
    (tftp / "grub" / "grub.cfg").write_text(render(session), encoding="utf-8")
    manifest = {"session": session.fingerprint, "profile": session.profile,
                "iso_path": str(iso), "iso_sha256": digest(iso), "guards": guards,
                "boot_files": {str(path.relative_to(tftp)).replace("\\", "/"): digest(path)
                               for path in (tftp / "casper/vmlinuz", tftp / "casper/initrd", destination)}}
    atomic_json(root / "image.json", manifest)
    # A new image preparation always invalidates old installation consent.
    for stale in ("diagnostic.json", "install.json"):
        (root / stale).unlink(missing_ok=True)
    return manifest
