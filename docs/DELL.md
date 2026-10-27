# Dell GB10 FCM1253: inspected media, experimental Ethernet recovery

Inspected on 2026-10-27. This is an independent community adapter. Dell's [official recovery guide](https://www.dell.com/support/kbdoc/en-us/000382042/how-to-reinstall-the-nvidia-dgx-operating-system-on-dell-pro-max-with-grace-blackwell-systems) documents USB recovery. **No physical Dell Ethernet reset has been completed for this project.** Preparation and image inspection are not hardware qualification.

## Official artifacts

Dell's [FCM1253 model downloads](https://www.dell.com/support/product-details/en-us/product/dell-pro-max-fcm1253-micro/drivers), under all model downloads, expose these June 12, 2026 OTA3.0 A00 images and SHA-256 values. Both complete 10,159,992,832-byte downloads matched the published hashes. Vendor media/binaries are not release assets.

| Variant | Dell driver ID | SHA-256 |
|---|---|---|
| [Worldwide ISO](https://dl.dell.com/FOLDER14602653M/1/Dell_Pro_Max_with_GB10_FCM1253_WW_DGX_OS7_OTA3_Recovery_Image.iso) | 5DHWK | `b359dfd877fa12f50e456e06212aa4426921149633e9a819ee58fa66eda6c7fa` |
| [US ISO](https://dl.dell.com/FOLDER14602659M/1/Dell_Pro_Max_with_GB10_FCM1253_US_DGX_OS7_OTA3_Recovery_Image.iso) | 65WCN | `1c2727a05f9dfdd9779e357dca7d86e7d6c3344a23cf23be5b3ae9e05caa5c34` |

The app matches Dell plus **FCM1253**, then asks for WW/US and explicit preview acknowledgement. A generic Dell/GB10 label, MAC prefix, PC language or geographic location cannot select an image. Consult the device's Dell support record when unsure. CLI users specify `init --profile dell-fcm1253-ota3-ww` or `dell-fcm1253-ota3-us`; normal network arguments remain required. The legacy interactive wizard defaults to ASUS and must not be used for Dell.

## Read-only inspection

- Both original GRUB configurations identify DGX OS 7.5.0. Their installation flags include `autoinstall`, `force-platform=dgx_spark`, `nopersistent` and the r8169 blacklist.
- Both initrds contain Casper `iso-url=*.iso` HTTP loading, `do_urlmount`, and `maybe_break casper-bottom`, plus modular r8127 Ethernet and NVMe drivers. The derived diagnostic observer stops before installation, hides NVMe, and reports checks; installation uses the untouched original initrd.
- Both include `/oemdata/oem-iso-cfg.sh`, OEM packages and Dell's `dell-dgx-wallpaper_1.0-2_all.deb` marker. Preparation checks the full regional ISO hash before extracting these files.
- In both NVIDIA SquashFS layers, `/ai/preseed.sh` is byte-identical and parses `force-bootdisk`. It sanitizes the device and falls back to an interactive installer when invalid. `/ai/dgx_setup.bash` uses that forced disk rather than automatic drive discovery; the preseed script substitutes its path into the installation configuration. This is offline code evidence, not a physical disk-mapping test.
- No vendor script was executed during inspection. No host network, firmware, target workload or SSD was modified.

## Remaining qualification

On actual Dell hardware, first verify the non-erasing diagnostic boot and observer report, firmware/Secure Boot behavior, target MAC and SSD identity. A destructive trial requires backups and separate erase consent. Record installation completion, regional first-run setup, first boot and host-network cleanup. Until those trials are recorded, the Dell path remains a media-inspected preview on every host OS.

NVIDIA [limits its Founders recovery media to Founders Edition](https://docs.nvidia.com/dgx/dgx-spark/system-recovery.html). Founders, ASUS, Dell and other OEM images are never interchangeable.
