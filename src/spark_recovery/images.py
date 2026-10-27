"""Verify pinned OEM images without mounting physical disks."""
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
from .profiles import ASUS, image_profile


def download(url: str, destination: Path, expected: str, *, progress=None) -> Path:
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
        total = int(response.headers.get("Content-Length") or 0)
        received = 0
        while chunk := response.read(4 * 1024 * 1024):
            output.write(chunk)
            received += len(chunk)
            if progress:
                progress({"stage": "Downloading " + destination.name, "received": received, "total": total})
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


def verify_bootloader(path: Path, profile: dict | None = None) -> None:
    profile = profile or ASUS
    if digest(path) != profile["bootloader_sha256"]:
        raise RecoveryError("Bootloader SHA256 changed; maintainer review required")
    data = path.read_bytes()
    if len(data) < 64 or data[:2] != b"MZ":
        raise RecoveryError("Invalid EFI executable")
    offset = struct.unpack_from("<I", data, 60)[0]
    if (offset + 6 > len(data) or data[offset:offset + 4] != b"PE\0\0"
            or struct.unpack_from("<H", data, offset + 4)[0] != 0xAA64):
        raise RecoveryError("Bootloader is not ARM64 PE/COFF")


def prepare(root: Path, session: Session, iso: Path | None = None, package: Path | None = None,
            bootloader: Path | None = None, *, progress=None, cache_dir: Path | None = None) -> dict:
    import pycdlib
    profile = image_profile(session.profile)
    if package is not None and "package_sha256" not in profile:
        raise RecoveryError("This OEM supplies a direct ISO; an ASUS package cannot be used")
    def stage(message):
        if progress:
            progress({"stage": message})
    stage("Checking available storage and official artifacts")
    root.mkdir(parents=True, exist_ok=True)
    if iso is None:
        assets = cache_dir or root / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        assets = assets / profile["id"]
        assets.mkdir(parents=True, exist_ok=True)
        iso = assets / "recovery.iso"
        if not iso.exists() or digest(iso) != profile["iso_sha256"]:
            if shutil.disk_usage(assets).free < 24 * 1024**3:
                raise RecoveryError("Allow at least 24 GiB free for recovery preparation")
            if "package_sha256" in profile:
                if package is None:
                    package = download(profile["url"], assets / "asus-package.zip", profile["package_sha256"], progress=progress)
                stage("Verifying package and extracting ASUS ISO")
                extract_package(package, iso)
            else:
                download(profile["url"], iso, profile["iso_sha256"], progress=progress)
    iso = iso.resolve()
    stage("Verifying full ISO SHA-256; this reads the entire image")
    if iso.stat().st_size != profile["iso_bytes"] or digest(iso) != profile["iso_sha256"]:
        raise RecoveryError("ISO does not match the pinned OEM/model/region profile; refusing to prepare it")
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
        if profile.get("marker"):
            image.get_record(rr_path=profile["marker"])
    finally:
        image.close()
    stage("Inspecting recovery kernel, drivers and diagnostic guards")
    guards = inspect_initrd(tftp / "casper" / "initrd")
    destination = tftp / "grubnetaa64.efi.signed"
    if bootloader:
        verify_bootloader(bootloader, profile)
        shutil.copyfile(bootloader, destination)
    else:
        download(profile["bootloader_url"], destination, profile["bootloader_sha256"], progress=progress)
        verify_bootloader(destination, profile)
    (tftp / "grub" / "grub.cfg").write_text(render(session), encoding="utf-8")
    stage("Writing verified manifest and diagnostic-only boot menu")
    manifest = {"session": session.fingerprint, "profile": session.profile,
                "iso_path": str(iso), "iso_sha256": digest(iso), "guards": guards,
                "boot_files": {str(path.relative_to(tftp)).replace("\\", "/"): digest(path)
                               for path in (tftp / "casper/vmlinuz", tftp / "casper/initrd", destination)}}
    atomic_json(root / "image.json", manifest)
    # A new image preparation always invalidates old installation consent.
    for stale in ("diagnostic.json", "install.json"):
        (root / stale).unlink(missing_ok=True)
    return manifest
