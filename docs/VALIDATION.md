# Validation record

## Hardware evidence

An operator-assisted reference workflow on 2026-10-23 used a Windows Ethernet host and ASUS Ascent GX10 with ASUS DGX OS 7.5.0-4 media. Evidence included:

- Vendor package SHA-256 and embedded ISO checksum verification.
- ARM64 PXE boot, matching-MAC DHCP OFFER/ACK, TFTP boot files and full HTTP ISO download.
- A deliberate initramfs stop with ISO9660 mounted read-only, OEM customization files present, and no NVMe device exposed.
- An operator-approved reinstall followed by a fresh working desktop.

The one-off scripts were subsequently replaced by this package. The package has **not** repeated the full destructive install on all three host OSes. Real machine logs, credentials, images and identifiers from that session are not included in the repository.

## Automated checks

The 0.3 suite additionally covers localized Windows adapter/neighbor parsing, default-gateway/Wi-Fi exclusions, bounded discovery, route-aware subnet allocation, authenticated IP-to-MAC evidence, multi-OEM routing/refusal, app-local host-key rotation, read-only observer generation, token-bound report handling and stale/failed evidence. These are simulated/protocol tests, not an end-to-end hardware claim. The derived diagnostic initrd was generated from the actual pinned ASUS media; booting that observer on real hardware is still a qualification step.

Desktop preview adds monotonic timing and interruption/sequence tests, noninteractive bounded SSH checks, helper operation/readiness tests, and an offscreen GUI smoke test. Desktop builds smoke-test the frozen executable on Windows x64 and Mac arm64/x64. These checks never request administrator permission, bind real DHCP, reboot a target or erase disks. They do not establish destructive end-to-end desktop qualification.

CI runs Python 3.11/3.12 on Windows, macOS and Linux. Tests exercise configuration/input rejection, firmware/kernel DHCP packets, OS interface-binding parameters, missing/stale consent, wrong OEM/MAC/SSD serial, safe archive extraction, boot-file mutation, advisory-lock exclusion, mocked network cleanup/failure paths, HTTP allowlisting and TFTP end-of-file/loss/duplicate ACK/rollover/path/upload behavior.

Protocol tests use ephemeral loopback ports. They do not bind real DHCP ports, change real interfaces, reboot targets or erase disks. A Windows runner without symlink privileges may skip that symlink fixture; traversal checks still run. Linux/macOS execute it when available.

The package's preparation path is additionally exercised locally against the actual pinned ASUS ISO and ARM64 bootloader. That is an artifact/inspection check, not a destructive recovery test. The official artifact hashes in `profiles.py` are pinned; no external recovery binary is committed.

## Qualification checklist for maintainers

Before advertising a host/hardware combination as qualified, record host OS/version, architecture, Python, Ethernet adapter/driver, target OEM/model, image version, UEFI/Secure Boot settings, diagnostic results, SSD identity mapping, installer completion and successful first boot. Redact serials, MACs, IPs, keys, tokens and personal paths before sharing evidence. Include cleanup tests after normal termination, initialization error and abnormal process exit.

The animated README and a green CI badge are presentation/automated-test evidence, not hardware qualification or a promise of error-free recovery.

## Dell media inspection (2026-10-27)

Both official Dell FCM1253 OTA3 WW and US ISO downloads matched Dell-published SHA-256 checksums. Full preparation, kernel/initrd extraction, Casper guards, original ARM64 bootloader validation and derived diagnostic observer generation were exercised against both images. Tests reject wrong OEM/model/region evidence. Offline installer inspection confirmed forced-disk handling. See [Dell evidence](DELL.md). No Dell hardware was rebooted or erased; physical network boot, installation and first boot remain unqualified.
