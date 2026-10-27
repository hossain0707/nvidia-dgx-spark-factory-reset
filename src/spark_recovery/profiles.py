"""Pinned public vendor artifacts. Vendor images/binaries are never redistributed."""
ASUS = {
    "id": "asus-gx10-7.5.0-4",
    "title": "ASUS Ascent GX10 DGX OS 7.5.0-4",
    "vendor": "ASUS",
    "oem": "asus-gx10",
    "url": "https://dlcdnets.asus.com/pub/ASUS/Desktop/Vivo_PC/GX10/ASUS-Ascent-GX10-OS-7.5.0-4-20260728071935.zip",
    "package_sha256": "6ca0a73f3c3050e6cae00ef375fb2831c07b2c7ff4d66f1e39ccfc8f8c000fb6",
    "iso_sha256": "ecfe8e1af89b9fad25711f3baa40a5af189581b3e0c66877690a18e8b4d8d912",
    "iso_bytes": 10821799936,
    "support": "https://www.asus.com/uk/support/faq/1057440/",
    "bootloader_url": "https://ports.ubuntu.com/ubuntu-ports/dists/jammy/main/uefi/grub2-arm64/current/grubnetaa64.efi.signed",
    "bootloader_sha256": "15061e7aa50b3847cfd1dac48fb6916553f5db550b54464cd1d0068fc2cd5e67",
}

DELL_WW = {
    "id": "dell-fcm1253-ota3-ww",
    "title": "Dell Pro Max GB10 FCM1253 DGX OS 7.5.0 OTA3 Worldwide",
    "vendor": "Dell",
    "oem": "dell-gb10",
    "region": "Worldwide (WW)",
    "url": "https://dl.dell.com/FOLDER14602653M/1/Dell_Pro_Max_with_GB10_FCM1253_WW_DGX_OS7_OTA3_Recovery_Image.iso",
    "iso_sha256": "b359dfd877fa12f50e456e06212aa4426921149633e9a819ee58fa66eda6c7fa",
    "iso_bytes": 10159992832,
    "support": "https://www.dell.com/support/kbdoc/en-us/000382042/how-to-reinstall-the-nvidia-dgx-operating-system-on-dell-pro-max-with-grace-blackwell-systems",
    "marker": "/oemdata/debs/dell-dgx-wallpaper_1.0-2_all.deb",
    "bootloader_url": ASUS["bootloader_url"],
    "bootloader_sha256": ASUS["bootloader_sha256"],
}
DELL_US = {**DELL_WW,
    "id": "dell-fcm1253-ota3-us",
    "title": "Dell Pro Max GB10 FCM1253 DGX OS 7.5.0 OTA3 US",
    "region": "United States (US)",
    "url": "https://dl.dell.com/FOLDER14602659M/1/Dell_Pro_Max_with_GB10_FCM1253_US_DGX_OS7_OTA3_Recovery_Image.iso",
    "iso_sha256": "1c2727a05f9dfdd9779e357dca7d86e7d6c3344a23cf23be5b3ae9e05caa5c34",
}

IMAGES = {profile["id"]: profile for profile in (ASUS, DELL_WW, DELL_US)}


def image_profile(identifier: str) -> dict:
    from .config import RecoveryError
    if identifier not in IMAGES:
        raise RecoveryError("Unknown recovery image profile; maintainer review required")
    return IMAGES[identifier]

