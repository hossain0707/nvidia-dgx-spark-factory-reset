# MKD Spark Recovery desktop

The app adds a guided interface and local timing reports to the existing recovery engine. Terminal commands remain available. **ASUS Ascent GX10 only; no Founders Edition or other OEM profile.** A separate computer, isolated Ethernet cable, and GX10 monitor/keyboard are required.

## Install

Use the official [MKD releases](https://github.com/mkd-hossain/nvidia-dgx-spark-factory-reset/releases) or [second repository releases](https://github.com/hossain0707/nvidia-dgx-spark-factory-reset/releases).

| Computer | Download |
|---|---|
| Windows x64 | `MKD-Spark-Recovery-0.2.0-Windows-x64-Setup.exe` |
| Apple Silicon Mac | `MKD-Spark-Recovery-0.2.0-macOS-arm64.dmg` |
| Intel Mac | `MKD-Spark-Recovery-0.2.0-macOS-x64.dmg` |

Compare the SHA-256 with `SHA256SUMS-*.txt`: Windows `Get-FileHash <installer> -Algorithm SHA256`; Mac `shasum -a 256 <download.dmg>`. Run the Windows installer, or drag the Mac app from the disk image to Applications. Launch the installed app. No Python installation is needed. SSH inspection requires the OS OpenSSH client and a previously trusted, working SSH key connection.

These are **unsigned community preview builds**, without publisher signing or Apple notarization. Windows SmartScreen or macOS Gatekeeper may block them. Verify origin and hash before approving them through the OS's supported UI; do not globally disable security controls. Production distribution still needs MKD's signing identities and hardware qualification.

## Guided workflow

1. **Connect & identify:** back up the GX10, connect isolated Ethernet, refresh the adapter list and select the dedicated wired adapter. Enter an unused private /24 subnet and the target MAC from UEFI. The defaults are examples, not discovery results. Creating a session performs no reboot or erase.
2. **Prepare & inspect:** accept vendor terms, download or select the official ISO, and wait for hash/driver checks. Download percentages count real bytes; hashing/extraction use an indeterminate indicator. Inspect using an existing trusted SSH-key connection, or import JSON saved from `target.py --json` on the GX10. The helper ships under `_internal/spark_recovery/target.py` on Windows and inside the Mac bundle's Resources/spark_recovery directory; source is also in this repository. See [target-local instructions](TARGET-AND-SSH.md). The app does not collect passwords or automatically trust host keys.
3. **Verify network boot:** start services and approve the native host administrator prompt. Windows ICS interruption is a separate checkbox; stop Mac Internet Sharing manually. Wait for **services ready**, then boot the target's PXE IPv4 entry and select VERIFY ONLY. Run the three displayed commands separately and confirm only their actual results.
4. **Authorize installation:** select the inspected whole SSD, type the exact MAC/serial phrase and enable the erase submenu. Re-enter PXE and select ERASE SSD, then CONFIRM ERASE at the GX10. Immediately mark installation started in the app when you select that final console confirmation. STOP remains default; no app button silently reboots or erases.
5. **Measure & finish:** keep power/Ethernet connected. Once the GX10 reaches fresh first-run setup or the new desktop, confirm that result, export the timing report, and stop services. Wait for host networking restoration before closing. Finish initial setup on the GX10 normally.

The [full recovery runbook](RECOVERY.md) is authoritative for firmware settings, diagnostic checks and stop conditions. Disk installation has no invented progress percentage. A quiet console or finished ISO transfer is not success evidence.

## Timing boundaries

Live intervals use the host's monotonic clock. UTC timestamps provide context; changing wall time does not change live durations.

| Measurement | Start | End |
|---|---|---|
| Preparation | Prepare action | Verified image and boot tree ready |
| Recovery session | Host services ready | Operator confirms first boot |
| Observed installation | Operator marks final console erase selection | Operator confirms first boot |
| Sidebar elapsed | Session creation | Session end/last observation |

Preparation includes download, extraction, verification, and waiting/retries after a failed attempt. Recovery-session time includes diagnostic boot, checks and user interaction. The observed installation interval includes the second ISO transfer and disk installation; operator reaction time affects it. Sidebar elapsed includes setup and waiting and is not an installation benchmark.

Successful reports are labelled `operator_confirmed_success`, not remotely certified. Failed/canceled runs do not receive a successful install duration. JSON exports omit MAC, IP, username, SSD serial and local paths. There is no analytics upload. Compare identical measurement boundaries, image and hardware when benchmarking; a single run cannot establish universal speed claims.

Reopening an active run marks timing `interrupted`; lost monotonic continuity is never reconstructed from wall time. Services may still be running. Reopen the session to inspect/finish recovery, but no continuous benchmark is invented for that run. For a new measurement, finish cleanup, close the app and create a new session.

## Errors and cleanup

- Canceling native administrator authorization does not start installation. Wait for the refusal/timeout and inspect status before retrying.
- Closure is blocked during active jobs/services. A GUI crash does not intentionally kill the separate helper. Reopen the same session folder.
- Lost heartbeat means uncertainty. Inspect the GX10 console and host processes; do not infer permission to reboot or kill an installer.
- Recording failure does not stop a writing installer. Removing erase authorization affects future boot menus only.
- Stop services only after confirming installation is complete/inactive. The helper uses the existing graceful server shutdown and scoped network journal.
- After a helper crash, confirm it is stopped before Restore networking. The existing service lock rejects competing restoration. Keep the journal until cleanup succeeds.
- Mac host firewall approval may be needed. The app does not globally disable the firewall or stop unrelated DHCP servers. Read the [Mac](MACOS.md) and [Windows](WINDOWS.md) network guides.

Sessions use the OS application-data location, under `MKD/Spark Recovery/sessions` in Windows Local AppData or the Qt app-data equivalent on Mac. Inventory and service logs contain identifiers: keep session folders private. Share only the sanitized timing export unless other files have been reviewed/redacted.

## Develop and build

```sh
python -m pip install -e ".[desktop,bundle,test]"
python -m spark_recovery.desktop
python -m spark_recovery.desktop --smoke-test
python packaging/make_icons.py
python -m PyInstaller --noconfirm packaging/desktop.spec
```

Build on each destination OS. Windows uses Inno Setup 6; Mac uses hdiutil. Desktop CI tests x64 Windows and arm64/x64 Mac frozen apps without network mutations. Mac builds run on macOS 15; earlier versions are not claimed as qualified. Windows ARM64 native binaries are not provided. Automated builds do not replace [hardware qualification](VALIDATION.md). See [desktop architecture](DESKTOP-ARCHITECTURE.md) and [third-party notices](../THIRD_PARTY_NOTICES.md).
