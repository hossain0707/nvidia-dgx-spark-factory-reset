"""Receipts bind diagnostic attestation and disk consent to one session/image."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re

from .config import RecoveryError, Session, atomic_json, digest, mac_address
from .menu import render
from .profiles import ASUS


def image_manifest(root: Path, session: Session, *, full: bool = False) -> dict:
    try:
        image = json.loads((root / "image.json").read_text(encoding="utf-8"))
        if image["session"] != session.fingerprint or image["profile"] != session.profile:
            raise RecoveryError("Image is bound to a different session; run prepare again")
        if image["iso_sha256"] != ASUS["iso_sha256"]:
            raise RecoveryError("Manifest does not identify the pinned ASUS image")
        if not image["guards"] or not all(image["guards"].values()):
            raise RecoveryError("Missing image diagnostic guards")
        for relative, expected in image["boot_files"].items():
            path = (root / "tftp" / relative).resolve()
            if not path.is_relative_to((root / "tftp").resolve()) or digest(path) != expected:
                raise RecoveryError("Boot artifact changed after preparation")
        iso = Path(image["iso_path"])
        if not iso.is_file() or (full and digest(iso) != image["iso_sha256"]):
            raise RecoveryError("ISO missing or changed")
        return image
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RecoveryError(f"Missing/invalid image verification: {error}") from error


def attest(root: Path, session: Session, confirmation: str):
    expected = "VERIFIED " + session.target_mac
    if confirmation != expected:
        raise RecoveryError("Read the diagnostic checklist, then confirm exactly: " + expected)
    image = image_manifest(root, session)
    atomic_json(root / "diagnostic.json", {
        "session": session.fingerprint, "image": image["iso_sha256"],
        "operator_attestation": True, "iso_read_only": True, "oem_present": True, "nvme_hidden": True,
        "at": datetime.now(timezone.utc).isoformat(),
    })


def validate_inventory(inventory: dict, session: Session, disk: str, serial: str):
    if inventory.get("machine") not in ("aarch64", "arm64") or not inventory.get("efi"):
        raise RecoveryError("Target inventory must describe an ARM64 UEFI machine")
    if not any(mac_address(value) == session.target_mac for value in inventory.get("interfaces", {}).values()
               if value != "00:00:00:00:00:00"):
        raise RecoveryError("Inventory MAC does not match the recovery target")
    board = " ".join(value for value in inventory.get("board", []) if isinstance(value, str)).lower()
    if "asus" not in board or "gx10" not in board:
        raise RecoveryError("Inventory does not identify an ASUS GX10; OEM mismatch or missing board evidence")
    if not re.fullmatch(r"/dev/nvme[0-9]+n[0-9]+", disk):
        raise RecoveryError("Use a whole NVMe namespace from the inspected target inventory")
    matches = [item for item in inventory.get("disks", [])
               if "/dev/" + item.get("name", "") == disk and item.get("type") == "disk"]
    if len(matches) != 1 or not serial.strip() or (matches[0].get("serial") or "").strip() != serial:
        raise RecoveryError("Disk/serial do not match the inspected target; refusing to expose erase")


def authorize(root: Path, session: Session, disk: str, serial: str, confirmation: str):
    image = image_manifest(root, session)
    try:
        receipt = json.loads((root / "diagnostic.json").read_text(encoding="utf-8"))
        inventory = json.loads((root / "target.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RecoveryError("Record target inventory and diagnostic attestation first") from error
    if receipt.get("session") != session.fingerprint or receipt.get("image") != image["iso_sha256"]:
        raise RecoveryError("Diagnostic receipt belongs to a different target/image")
    agent_proof = (receipt.get("agent_verified") is True and image.get("diagnostic_agent", {}).get("session") == session.fingerprint
                   and receipt.get("agent_token_sha256") == image.get("diagnostic_agent", {}).get("token_sha256")
                   and bool(receipt.get("agent_token_sha256")))
    if not (receipt.get("operator_attestation") is True or agent_proof) or not all(receipt.get(key) is True for key in
               ("iso_read_only", "oem_present", "nvme_hidden")):
        raise RecoveryError("Diagnostic checklist is incomplete")
    validate_inventory(inventory, session, disk, serial)
    expected = f"ERASE {session.target_mac} {serial}"
    if confirmation != expected:
        raise RecoveryError("To approve SSD erasure, confirm exactly: " + expected)
    consent = {"session": session.fingerprint, "image": image["iso_sha256"],
               "disk": disk, "serial": serial, "at": datetime.now(timezone.utc).isoformat()}
    atomic_json(root / "install.json", consent)
    write_menu(root, session, disk)


def write_menu(root: Path, session: Session, disk: str = ""):
    destination = root / "tftp/grub/grub.cfg"
    temporary = destination.with_suffix(".cfg.pending")
    agent = False
    if (root / "image.json").exists():
        image = json.loads((root / "image.json").read_text(encoding="utf-8"))
        agent = image.get("diagnostic_agent", {}).get("session") == session.fingerprint
    temporary.write_text(render(session, disk, agent=agent), encoding="utf-8", newline="\n")
    temporary.replace(destination)


def check_active_menu(root: Path, session: Session, image: dict):
    menu = (root / "tftp/grub/grub.cfg").read_text(encoding="utf-8")
    agent = image.get("diagnostic_agent", {}).get("session") == session.fingerprint
    if menu == render(session, agent=agent):
        return
    try:
        consent = json.loads((root / "install.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RecoveryError("Unrecognized menu or missing install authorization; run revoke-install") from error
    if (consent.get("session") != session.fingerprint or consent.get("image") != image["iso_sha256"]
            or menu != render(session, consent["disk"], agent=agent)):
        raise RecoveryError("Menu or authorization changed; run revoke-install and inspect state")

