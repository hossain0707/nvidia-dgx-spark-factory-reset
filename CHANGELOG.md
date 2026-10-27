# Changelog

## 0.3.1 Windows helper status hotfix — 2026-10-27

- Grant the session owner's explicit Windows SID inheritable access before elevation, so output written by a different administrator remains readable by the normal app. Do not grant broad Users/Everyone access.
- Report unreadable or malformed helper status as an actionable readiness failure instead of silently treating it as an absent helper.
- Display helper startup, full image verification, host-network configuration and service binding stages separately. Readiness is still reported only after services start.
- Use pointer-sized Windows ShellExecute return values and retain separate network, reboot and erase consent gates.
- Handle the address disappearing when ICS stops, make missing firewall-rule cleanup idempotent, and return explicit PowerShell success/failure with readable error messages.

Existing sessions containing administrator-owned files may need a scoped administrator permission repair and saved-network restoration. This hotfix does not by itself qualify Dell hardware or fix an unrelated network-service error hidden in an unreadable old log.

## 0.3.0 discovery and OEM preview — 2026-10-27

- Detect wired adapters, scoped IPv4/IPv6 neighbors and SSH endpoints; bound isolated-link searches and avoid Wi-Fi/default-gateway guesses.
- Authenticate with explicit host-key trust and ephemeral password/key support; fill device MAC, model, SSD identity and collision-aware network plans from evidence.
- Route ASUS, Dell GB10, Founders, HP ZGX, Lenovo PGX and MSI EdgeXpert identities through separate capability records; block unqualified or mismatched images.
- Reuse verified images, serialize shared cache preparation, offer specific network/reboot/erase consents, and automatically clean up after confirmed first boot.
- Add an optional read-only OEM Casper RAM diagnostic observer with session/image/token-bound results. Retain the original installer initrd and final console erase approval.
- Fix UTF-8 command output on localized Windows and use the actual Windows InterfaceType property for Ethernet detection.
- Add independent Dell FCM1253 OTA3 WW / US profiles after downloading and verifying both official ISOs, inspecting their Casper drivers/stops and forced-disk installer flow, and exercising preparation against both. Require an explicit regional selection and preview acknowledgement. Physical Dell Ethernet reset qualification remains outstanding.
- Bundle SSH dependencies with third-party notices and statically link Intel Mac cryptography to avoid frozen OpenSSL conflicts.

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
