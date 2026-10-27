# Security and data safety

Desktop: the Qt GUI stays unelevated; native OS authorization launches only the fixed serve/restore helper. Local session owners are trusted. Status and timing files do not remotely certify a reset. Install from official releases and verify hashes. Read [desktop recovery/cleanup behavior](docs/DESKTOP.md). Current binaries are unsigned previews without publisher signing or Apple notarization.

This tool can expose an installer that permanently erases an SSD. Use it only on equipment you are authorized to recover and only after backups. Host-side diagnostics and checksums do not guarantee every later installation step will succeed.

## Boundary

- Only the pinned ASUS GX10 image profile is implemented. Vendor artifacts are downloaded directly and hash-checked; none are redistributed here.
- Use an isolated Ethernet link. TFTP and recovery HTTP are unauthenticated LAN protocols. MAC/IP allowlists are not cryptographic authentication and can be spoofed by a hostile participant.
- Explicit permissions, matching inventory/serial, diagnostic receipts and a cancel-first menu reduce accidental activation. Local state is not tamper-proof against an administrator.
- State contains hardware identifiers and local paths. Do not commit `state/`, inventories, logs, images, private keys, tokens or credentials. There is no external analytics upload; diagnostic observations stay on the isolated recovery link.
- The automatic flow can accept a transient target SSH/sudo password; it is not persisted to disk or placed in shell arguments. Existing keys can be used instead. No `StrictHostKeyChecking=no`, silent known-host deletion, global firewall disabling, host partition commands or unattended default erase is added. Explicit verified key changes affect only the app-local trust store.
- Existing SSH privilege is reused; the project does not grant blanket passwordless sudo. Target-local root BootNext/reboot is explicit.
- The optional RAM observer has no command, erase, firmware-write or reboot endpoint. It is added to a derived diagnostic initrd only; installation uses the original initrd. Reports are bound to source IP, private session token and image. They do not prove successful installation or protect against a hostile isolated-link participant who steals the token. See [automation boundaries](docs/AUTOMATION.md).

## Reporting

For a suspected vulnerability, do not include credentials, device identifiers, private data or a working exploit in a public issue. Use GitHub's private vulnerability reporting if enabled on the primary repository; otherwise open a minimal public request for a private contact without disclosing sensitive details. Ordinary sanitized bugs can use the issue template.

Commercial/organizational use and redistribution require MKD's prior written permission under [LICENSE](LICENSE). A security report does not grant such permission.
