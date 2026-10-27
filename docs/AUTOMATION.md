# Automatic discovery and OEM-specific recovery (0.3 preview)

The app removes fixed-PC/fixed-IP assumptions. It does not remove authentication, device compatibility checks or conscious erase approval. **Device detection is broader than supported Ethernet installation.** A detected Dell is not permission to run an ASUS image.

## Default automatic workflow

1. Connect a dedicated Ethernet cable and open the app. It detects active wired interfaces on Windows/macOS/Linux, excludes Wi-Fi from automatic selection, and prefers a link without a default gateway. If multiple links are possible, choose the correct adapter; the app does not guess.
2. Neighbor discovery and a bounded IPv6 link-local multicast query look for SSH endpoints on that interface. If necessary, confirm that this is an isolated link to permit one bounded IPv4 search (at most 256 candidates). A large corporate subnet never becomes an enterprise-wide scan. Background refresh uses cached-neighbor checks rather than repeatedly scanning every host.
3. Select the endpoint if several respond. A banner is labelled **unverified**, never “ASUS” based on its MAC/vendor prefix. Existing SSH trust is checked. For a new key, compare its fingerprint on the physical target and approve it. A changed key has a distinct warning and typed confirmation; global OpenSSH trust is not silently deleted or changed.
4. Sign in with an existing SSH key or the account/password on the target. A matching user in your SSH config is suggested; usernames and passwords are not guessed. Passwords may exist transiently in process memory but are not written to session files or logs. The app cannot enable a disabled SSH server or bypass the target's login.
5. Authenticated read-only inventory binds the reachable address to an interface MAC and reads manufacturer/model, ARM64/UEFI evidence, SSD paths and serials. Conflicts, unknown devices and ambiguous address-to-MAC mappings stop preparation. Single-SSD details are filled automatically; review any multiple-SSD choice.
6. The OEM registry selects the manufacturer's path. ASUS GX10 uses its own pinned package; Dell FCM1253 uses its separate direct ISO profiles and requires an explicit WW/US regional choice and preview acknowledgement. The app reuses previously verified matching media when available, otherwise downloads vendor-pinned artifacts into a shared, locked cache after acceptance of vendor terms. Full verification still occurs; a filename/cache hit is not trust evidence.
7. A clear consent panel describes the exact wired interface, temporary address/lease, DHCP/TFTP/HTTP services, Windows ICS interruption and restoration. Only after approval does the native OS administrator prompt launch the narrow service helper.
8. Once services are ready, a separate reboot consent explains workload interruption and requests target sudo authorization if needed. It sets the uniquely matching IPv4 PXE BootNext, not a hardcoded entry number or permanent boot order. Firmware/Secure Boot restrictions still require target-console attention; the app does not bypass them.
9. Supported Casper diagnostic mode automatically selects **VERIFY ONLY** after five seconds. STOP remains available. A temporary RAM observer reports read-only ISO mounting, OEM content, hidden NVMe and the matching MAC to a session-token-bound, source-IP-restricted endpoint. Missing, stale or failed evidence keeps installation blocked. The observer has no erase/reboot/firmware-write commands and is absent from the original installer initrd. Manual console checks remain an explicit fallback.
10. Permanent SSD erasure requires a separate comprehension dialog: exact target MAC, whole SSD path, serial and image; three acknowledgements; and typing `ERASE <SSD serial>`. Only then is the cancel-first install menu exposed. **The final destructive boot still requires confirmation at the target console.** The installation entry uses the untouched original initrd, never the diagnostic observer.
11. Mark the final console erase selection to start the observed installation interval. Confirm fresh first-run setup/desktop to finish the report. The automatic flow removes future erase authorization and requests graceful host-service cleanup after this confirmation. A transfer or ping never certifies a completed reset.

## What cannot be inferred or bypassed

- A physical cable alone does not supply an SSH account/password, prove manufacturer identity, enable an OS service, or change locked firmware.
- A powered-off device, disabled SSH, an unconfigured first-boot account, blocked multicast/firewall or completely incompatible addressing may need local setup. The app gives a stop/help message rather than guessing credentials or scanning unrelated interfaces.
- A factory-reset OS has new trust/credentials and may not expose authenticated remote installation status. First boot remains an operator observation. Only verified stage boundaries are timed; no fake percentage/success is generated.
- Restoring the host's network can change the target's subsequent DHCP address. Internet access/first-run Wi-Fi setup is separate from local recovery. The app does not enable Internet Sharing globally without consent.
- Firmware and final destructive console approval are deliberate remaining interactions. This preview is not advertised as fully unattended recovery.

## Manufacturer adapters

| Detected system | Current path | Ethernet erase support |
|---|---|---|
| ASUS Ascent GX10 | Pinned ASUS ISO; Casper diagnostic/installation adapter | Preview; original reference procedure verified, new automatic flow still needs hardware qualification |
| Dell Pro Max with GB10, FCM1253 | Separate pinned WW / US OTA3 Casper profiles; exact model match and explicit regional choice | Media-inspected preview; no completed physical Dell reset test |
| NVIDIA DGX Spark Founders Edition | NVIDIA FastOS archive and official recovery/PXE guidance | Separate adapter required; never use ASUS/Dell media |
| HP ZGX Nano G1n, Lenovo ThinkStation PGX, MSI EdgeXpert | Model recognition and the manufacturer's support path | Vendor-specific media/adapter qualification required |
| Unknown/conflicting identity or non-ARM64 machine | Refusal | Blocked |

Dell's [official GB10 reinstall procedure](https://www.dell.com/support/kbdoc/en-us/000382042/how-to-reinstall-the-nvidia-dgx-operating-system-on-dell-pro-max-with-grace-blackwell-systems) documents USB installation. Public model downloads also expose WW and US images and SHA-256 checksums. Both actual downloads were checksum-verified and inspected for HTTP loading, diagnostic stops, NIC/storage modules, OEM content and disk targeting. This supports a separate experimental Casper adapter; it does not establish Dell endorsement or a completed hardware reset. See [inspection evidence](DELL.md). Images are downloaded from Dell and never redistributed.

NVIDIA explicitly limits its [Founders recovery procedure](https://docs.nvidia.com/dgx/dgx-spark/system-recovery.html) to Founders Edition and notes OEM media comes from the respective OEM. Its [PXE reference](https://docs.nvidia.com/dgx/dgx-spark/pxe.html) uses a different recovery layout. Other manufacturer's logos/model names are not evidence that those procedures apply.

## Diagnostic observer boundary

The downloaded OEM ISO and original initrd remain hash-pinned. The app creates a second diagnostic initrd by appending a small newc archive: it preserves the original early-hook ORDER, adds a background read-only observer and records the derived file's hash in the session manifest. Only diagnostic boot uses this derived file and retains NVMe blacklists, `nopersistent`, `break=casper-bottom` and `init=/bin/sh`. Install uses the original file and cancel-first menu.

The pinned initramfs has a minimal GET-only wget. The observer uses a narrow token-and-four-bit report endpoint; it accepts no commands, file paths or user data. Tokens are redacted from request logs. Session/image/token bindings and exact booleans are checked before an agent receipt is accepted. A failed authenticated report invalidates old diagnostic/install receipts. This is evidence within a trusted isolated link, **not cryptographic hardware attestation against a hostile LAN**.

## Qualification still required

Tests cover platform parsers, scoped/bounded discovery, subnet allocation, OEM/region mismatches, key trust/rotation, diagnostic token failures and absence of destructive observer code. Actual pinned ASUS and Dell media were inspected offline. Build and smoke tests do not prove a real boot of a derived diagnostic image. Complete a non-erasing diagnostic hardware trial before any reset test. Dell's adapter is explicitly an unqualified preview; record firmware behavior, disk targeting and first boot before describing any Dell hardware combination as verified.
