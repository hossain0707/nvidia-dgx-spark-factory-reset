# Ethernet recovery runbook

This procedure erases an SSD only after explicit authorization. Use a dedicated cable or isolated recovery VLAN, not a shared office LAN. The operator needs a GX10 monitor and keyboard for the menus. A USB keyboard/hub is fine; **no USB storage drive is needed**.

## 1. Inspect and prepare

Back up the target's data before installing. Record its **Ethernet MAC** from UEFI, exact model, SSD path and serial. Confirm you are using the ASUS GX10 profile, not Founders Edition or another OEM's machine. Ensure PXE IPv4 is enabled. The tested GRUB loader required Secure Boot disabled; do not change firmware trust settings without understanding your policy.

Use `wizard` or `init`, then `prepare --download`. Select an unused private subnet. Example addresses here are `172.31.250.1/24` (host) and `172.31.250.2` (GX10); replace all example MACs/interfaces. No automatic changes to Wi-Fi/default routes are intended.

Capture target identity **before** booting the diagnostic environment, where NVMe will be hidden:

```sh
python -m spark_recovery inspect-target --ssh operator@TARGET_ADDRESS
```

If SSH is unavailable, follow [target-local inventory](TARGET-AND-SSH.md). Inspect `state/target.json`: the detected ARM64 UEFI board must identify ASUS/GX10; the MAC and SSD serial must belong to this specific device. Missing OEM evidence is a stop condition, not a reason to substitute a profile.

`doctor` checks image integrity, menu state, server address and port conflicts. A server address may be absent until you use managed networking. Resolve unrelated port-67/69/8080 services first. Never run two DHCP servers on this link. Firewall-specific steps are in the OS guides.

## 2. Start the host services

Open an elevated host terminal and keep it open. See the OS guides for the correct Python path and privileges:

```sh
python -m spark_recovery serve --manage-network
```

Windows with existing Internet Connection Sharing needs the explicit `--stop-ics` flag. This temporarily interrupts the target's access through ICS; HTTP/TFTP/DHCP recovery remains local. On macOS/Linux the adapter adds an address alias and removes only that added address on cleanup. Existing DHCP services are not silently disabled.

Keep the host plugged into power and prevent lid-close/idle sleep. Windows requests idle-sleep inhibition; macOS uses `caffeinate`; Linux operators should use `systemd-inhibit` or their desktop's sleep settings. Those mechanisms cannot prevent power loss, cable disconnects or all forced sleep events.

## 3. Diagnostic PXE boot

Handle running workloads before an explicit reboot. Select the GX10's UEFI PXE IPv4 entry manually, or use [one-time BootNext](TARGET-AND-SSH.md). Do not hardcode a boot-entry number from someone else's machine.

At GRUB, select **VERIFY ONLY**. It downloads the ISO into RAM, blocks `nvme`/`nvme_core`, uses `nopersistent`, omits `autoinstall`, stops at `break=casper-bottom`, and uses `init=/bin/sh` as a further guard against starting systemd if the break shell is exited.

At `(initramfs)`, run separately:

```sh
mount | grep /cdrom
ls /cdrom/oemdata
ls /dev/nvme*
```

Expected results:

- `/cdrom` is an ISO9660 loop mount with **`ro`**.
- OEM content includes **`oem-iso-cfg.sh`** and **`debs`**.
- `/dev/nvme*` reports **No such file or directory**.

An `(initramfs)` prompt is intentional in this mode. Do not type `exit` as a way to start installation. If checks fail, stop and investigate. Do not alter the guard flags to make a failing diagnostic look successful.

On the host, in a **second terminal** using the same state directory, record only checks you actually completed:

```sh
python -m spark_recovery attest-diagnostic --confirm "VERIFIED 02:00:00:00:00:01"
```

Use the target's normalized lowercase colon-separated MAC. This is an operator attestation, not an automated remote certification.

## 4. Explicit erase authorization

Review the previously captured SSD path and serial again. The following example does **not** use a real device serial:

```sh
python -m spark_recovery enable-install \
  --disk /dev/nvme0n1 --serial YOUR_SSD_SERIAL \
  --confirm "ERASE 02:00:00:00:00:01 YOUR_SSD_SERIAL"
```

The tool refuses missing diagnostic receipts, OEM mismatch, mismatched MAC, disk partitions instead of whole namespaces, and wrong serials. The disk serial must come from inventory, not a guessed value. The menu still defaults to **STOP**, indefinitely.

To enter PXE again from the diagnostic shell, use `reboot -f` only while no installer is running, then select PXE from firmware. If that boots the untouched installed OS, log in and use the one-time BootNext command documented in the target guide. Do not simply exit the diagnostic shell.

At the new GRUB menu select **ERASE SSD**, then **CONFIRM ERASE … START INSTALLATION**. This is the final irreversible choice. It boots with the ASUS installation flags, the approved whole-disk path, and NVMe enabled. It can now overwrite partitions and data. It is an OS reinstall, not a promise to downgrade every firmware component or securely sanitize every flash cell.

## 5. Installation and completion

Do not restart either machine, stop the host services, or disconnect Ethernet while installation is running. ISO download progress is not evidence that disk installation is complete. Photograph error messages and inspect the installer console; host transfer logs cannot prove successful SSD installation.

After the installer reports completion and the GX10 boots into its initial setup/new desktop:

```sh
python -m spark_recovery revoke-install
```

Then stop the serving terminal with **Ctrl+C**. Managed host networking is restored automatically. If the host program was forcibly stopped, run `restore` as administrator/root **after confirming all recovery processes are stopped**. Keep the state journal until restoration succeeds.

Complete first-run setup, choose the desired account/hostname, and verify the installed OS/model, storage and network. Re-pair NVIDIA Sync or SSH using the new credentials. Reinstallation normally changes SSH host keys: verify the new fingerprint locally before updating an old trusted entry. The tool never deletes host keys automatically. An old IP is not guaranteed after returning to normal DHCP.

## Stop conditions

Unexpected target/OEM, checksum mismatch, wrong MAC/serial, competing DHCP, failed diagnostic guards, wrong SSD visibility, or an installer error all require investigation. Do not reboot an actively writing installer just because the screen is quiet. There is no error-free universal recovery guarantee.
