# Security and data safety

This tool can expose an installer that permanently erases an SSD. Use it only on equipment you are authorized to recover and only after backups. Host-side diagnostics and checksums do not guarantee every later installation step will succeed.

## Boundary

- Only the pinned ASUS GX10 image profile is implemented. Vendor artifacts are downloaded directly and hash-checked; none are redistributed here.
- Use an isolated Ethernet link. TFTP and recovery HTTP are unauthenticated LAN protocols. MAC/IP allowlists are not cryptographic authentication and can be spoofed by a hostile participant.
- Explicit permissions, matching inventory/serial, diagnostic receipts and a cancel-first menu reduce accidental activation. Local state is not tamper-proof against an administrator.
- State contains hardware identifiers and local paths. Do not commit `state/`, inventories, logs, images, private keys, tokens or credentials. No telemetry is sent by this project.
- No password or credential collection, `StrictHostKeyChecking=no`, automatic known-host deletion, global firewall disabling, partition commands on the host, or unattended default erase.
- Existing SSH privilege is reused; the project does not grant blanket passwordless sudo. Target-local root BootNext/reboot is explicit.

## Reporting

For a suspected vulnerability, do not include credentials, device identifiers, private data or a working exploit in a public issue. Use GitHub's private vulnerability reporting if enabled on the primary repository; otherwise open a minimal public request for a private contact without disclosing sensitive details. Ordinary sanitized bugs can use the issue template.

Commercial/organizational use and redistribution require MKD's prior written permission under [LICENSE](LICENSE). A security report does not grant such permission.
