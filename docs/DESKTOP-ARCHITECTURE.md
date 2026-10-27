# Desktop architecture

Version 0.3 adds a scoped discovery layer, manufacturer capability registry and explicit app-local key trust/rotation. It can use transient device SSH/sudo passwords without storing them. Network, reboot and erase consents are separate. A derived diagnostic initrd contains only a read-only temporary observer; the original installer initrd and final console erase gate remain intact. The older key-only/manual path below is still available. Read [the updated automation design](AUTOMATION.md).

The optional PySide6 Widgets frontend calls the existing Session, prepare, safety and inventory APIs. Terminal entry points and erase guards remain intact. Preparation adds an optional progress callback; default CLI behavior stays the same.

```mermaid
flowchart LR
  UI[Unelevated Qt desktop] --> Worker[Background preparation / strict SSH]
  Worker --> Engine[Existing verification engine]
  UI -->|Native OS authorization| Helper[Fixed serve / restore helper]
  Helper --> Server[Existing DHCP / TFTP / HTTP]
  Helper --> Journal[Network journal and service lock]
  Helper -->|Local status and heartbeat| UI
  UI --> Timing[Monotonic observations]
  Timing --> Report[Sanitized JSON report]
  Console[GX10 console operator] -->|Diagnostic and first-boot attestations| UI
```

The GUI stays unelevated. Only the fixed serve/restore helper requests native host OS authorization. It accepts no arbitrary shell fragment or host-disk operation. Target sudo is a separate authenticated, consented BootNext/reboot command. Unix arguments are shell-quoted before the macOS authorization call; Windows uses native argument quoting. Install the app into a trusted location before granting privileges. Source-mode elevation is intended for maintainers.

The local session owner is trusted, matching the CLI boundary. Local status is not authenticated proof of SSD progress. DHCP/transfer allowlists are not a defense against a hostile shared LAN. Host mutations use the existing journal and service lock. Failure cleanup runs through the existing code path.

Long preparation, discovery and bounded SSH calls run on QThread; Qt signals update the UI. Key trust always requires existing trust or explicit fingerprint verification. Optional passwords stay transient; no credential is placed in a process argument or session file. Download percentages are byte-based; hashing and target disk installation get no invented percentage. A GUI crash leaves the separate helper serving rather than automatically interrupting an installer. Reopening the app marks continuous timing interrupted.

Timing records monotonic boundaries and contextual UTC, labels operator observations and exports no target identifiers. HTTP 200, PXE traffic and pings are never interpreted as successful installation.

Onedir packaging keeps Qt libraries replaceable, with license texts, source pointers and notices. Destination-platform CI smoke-tests frozen apps. Publisher signing, Apple notarization and independent destructive hardware qualification remain required before a production label; no signing identity is fabricated.
