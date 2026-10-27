"""Manufacturer-specific recovery capabilities; never substitute another OEM's image."""
from __future__ import annotations

from dataclasses import dataclass
import re

from .config import RecoveryError
from .profiles import ASUS


@dataclass(frozen=True)
class OEMProfile:
    id: str
    manufacturer: str
    model: str
    transport: str
    image_profile: str | None
    recovery_url: str
    downloads_url: str
    status: str
    explanation: str


PROFILES = {
    "asus-gx10": OEMProfile("asus-gx10", "ASUS", "Ascent GX10", "casper-pxe", ASUS["id"], ASUS["support"],
        "https://www.asus.com/networking-iot-servers/desktop-ai-supercomputer/asus-ascent/asus-ascent-gx10/helpdesk_download/",
        "preview", "Pinned ASUS image and diagnostic-first PXE adapter. Hardware qualification remains required."),
    "dell-gb10": OEMProfile("dell-gb10", "Dell", "Pro Max / Pro Precision with GB10 (FCM1253)", "vendor-guided", None,
        "https://www.dell.com/support/kbdoc/en-us/000382042/how-to-reinstall-the-nvidia-dgx-operating-system-on-dell-pro-max-with-grace-blackwell-systems",
        "https://www.dell.com/support/product-details/en-us/product/dell-pro-max-fcm1253-micro/drivers",
        "vendor-media-required", "Dell documents service-tag-specific ISO download and USB recovery. "
        "Ethernet installation is not qualified here. ASUS or Founders media must not be substituted."),
    "nvidia-founders": OEMProfile("nvidia-founders", "NVIDIA", "DGX Spark Founders Edition", "fastos-pxe", None,
        "https://docs.nvidia.com/dgx/dgx-spark/system-recovery.html",
        "https://www.nvidia.com/en-us/drivers/dgx-spark-recovery-software/",
        "adapter-required", "Founders uses its own FastOS tar archive and firmware flow, not the ASUS ISO. "
        "The official PXE procedure needs a separately qualified adapter."),
    "hp-zgx": OEMProfile("hp-zgx", "HP", "ZGX Nano G1n", "vendor-guided", None,
        "https://support.hp.com/", "https://support.hp.com/drivers", "vendor-media-required",
        "Obtain the HP recovery image for the exact product. No ASUS/Dell/Founders image substitution is permitted."),
    "lenovo-gb10": OEMProfile("lenovo-gb10", "Lenovo", "ThinkStation PGX", "vendor-guided", None,
        "https://support.lenovo.com/", "https://pcsupport.lenovo.com/", "vendor-media-required",
        "Use Lenovo's model-specific recovery path; no qualified Ethernet image adapter is installed."),
    "msi-edge": OEMProfile("msi-edge", "MSI", "EdgeXpert", "vendor-guided", None,
        "https://www.msi.com/support", "https://www.msi.com/support", "vendor-media-required",
        "Use MSI's model-specific recovery path; no qualified Ethernet image adapter is installed."),
}


def detect_oem(inventory: dict) -> OEMProfile:
    if inventory.get("machine") not in ("aarch64", "arm64"):
        raise RecoveryError("Connected device is not an ARM64 Spark-class target")
    board = inventory.get("board", [])
    if not isinstance(board, list) or not all(isinstance(value, str) for value in board):
        raise RecoveryError("Missing manufacturer/model evidence")
    text = " ".join(board).replace("\0", " ").lower()
    # Manufacturer conflicts are a refusal, never a first-match override.
    vendors = [name for name, pattern in (("asus", r"\basus\b|asustek"), ("dell", r"\bdell\b"),
               ("hp", r"\bhp\b|hewlett"), ("lenovo", r"\blenovo\b"), ("msi", r"\bmsi\b|micro.star"))
               if re.search(pattern, text)]
    if len(vendors) > 1:
        raise RecoveryError("Conflicting manufacturer evidence; automatic recovery is blocked")
    if vendors == ["asus"] and "gx10" in text:
        return PROFILES["asus-gx10"]
    if vendors == ["dell"] and ("gb10" in text or "fcm1253" in text):
        return PROFILES["dell-gb10"]
    if vendors == ["hp"] and "zgx" in text:
        return PROFILES["hp-zgx"]
    if vendors == ["lenovo"] and "pgx" in text:
        return PROFILES["lenovo-gb10"]
    if vendors == ["msi"] and "edgexpert" in text:
        return PROFILES["msi-edge"]
    founders_dmi = (inventory.get("manufacturer", "").strip().lower() in ("nvidia", "nvidia corporation")
                   and "dgx spark" in inventory.get("product", "").lower())
    if not vendors and "nvidia" in text and "dgx spark" in text and ("founders" in text or founders_dmi):
        return PROFILES["nvidia-founders"]
    raise RecoveryError("Manufacturer/model is not recognized unambiguously. No recovery image will be selected")


def ethernet_profile(inventory: dict) -> OEMProfile:
    profile = detect_oem(inventory)
    if not profile.image_profile:
        raise RecoveryError(profile.manufacturer + ": " + profile.explanation)
    return profile
