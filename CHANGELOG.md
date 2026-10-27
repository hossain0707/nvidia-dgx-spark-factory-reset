# Changelog

## 0.3.0 discovery and OEM preview — 2026-10-27

- Detect wired adapters, scoped IPv4/IPv6 neighbors and SSH endpoints; bound isolated-link searches and avoid Wi-Fi/default-gateway guesses.
- Authenticate with explicit host-key trust and ephemeral password/key support; fill device MAC, model, SSD identity and collision-aware network plans from evidence.
- Route ASUS, Dell GB10, Founders, HP ZGX, Lenovo PGX and MSI EdgeXpert identities through separate capability records; block unqualified or mismatched images.
- Reuse verified images, serialize shared cache preparation, offer specific network/reboot/erase consents, and automatically clean up after confirmed first boot.
- Add an optional read-only ASUS RAM diagnostic observer with session/image/token-bound results. Retain the original installer initrd and final console erase approval.
- Fix UTF-8 command output on localized Windows and use the actual Windows InterfaceType property for Ethernet detection.
- Keep Dell Ethernet installation blocked pending Dell media/adapter/hardware qualification; vendor guidance is not a reset-support claim.

## 0.2.0 desktop preview — 2026-10-26

- Optional Windows/macOS desktop frontend; existing terminal recovery engine retained.
- Guided setup, real download progress, strict SSH/import inventory, diagnostic checks and exact SSD authorization.
- Separate elevated helper with heartbeat and existing journaled network restoration.
- Monotonic timing, interrupted-run detection and identifier-free reports; no transfer-to-success inference.
- Windows x64 installer and Apple Silicon/Intel Mac DMG builds, frozen-app smoke tests and SHA-256 manifests.
- Preview status until publisher signing, notarization and full hardware qualification are complete.

## 0.1.0 — community preview

- Parameterized Windows/macOS/Linux host CLI and isolated Ethernet plan.
- Pinned ASUS GX10 7.5.0-4 image/bootloader verification and initrd inspection.
- Diagnostic-first GRUB with NVMe blocking and explicit cancel-first erase submenu.
- Target MAC/SSD serial consent, strict SSH preflight and matching-MAC BootNext detection.
- Read-only HTTP/TFTP, firmware/kernel DHCP and journaled host restoration.
- Automated protocol/safety tests, cross-platform CI, platform guides and animated SVG README.
- MKD Personal Use and Permission-Required License.

The packaged rewrite remains preview software. The reference Windows/GX10 recovery workflow was hardware-verified; additional packaged-host hardware qualification remains outstanding.
