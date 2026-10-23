# Troubleshooting

| Symptom | Check / next step |
|---|---|
| PXE waits then returns to installed OS | Inspect host DHCP logs. Match NIC MAC/ARM64 architecture, dedicated interface, firewall UDP 67, and limited broadcast routing. Re-arm one-time BootNext for a retry; it is consumed after a boot. |
| DHCP works, no TFTP | Confirm target-to-host UDP 69 and ephemeral server ports; check the configured target IP and bootloader hash. UEFI behavior differs from a running Linux OS firewall. |
| TFTP works, kernel cannot obtain a lease | Recovery kernel sends ordinary DHCP after PXE. Ensure the same target MAC is permitted for both and no competing DHCP server is present. |
| ISO download fails | Check HTTP port, literal server IPv4, Ethernet driver, host power/sleep, target lease and file length/hash. Never retry by choosing an unverified image. |
| `(initramfs)` in VERIFY ONLY | Expected at the deliberate break. Follow the three diagnostic commands in the runbook. Do not type exit to install. |
| GRUB keyboard does not respond | Use wired/USB keyboard mode, dedicated arrow keys, and a firmware-compatible hub/port. A charging cable alone may not switch a wireless keyboard into wired input. |
| GRUB `read` command missing | The shipped menu does not use `read`; it uses a cancel-first submenu. Confirm you loaded this release's generated menu, not an older one-off file. |
| Host port conflict | Stop/reconfigure the specific existing service yourself. Never launch another DHCP server on a shared LAN. Windows ICS requires explicit managed `--stop-ics`. |
| Host exited before cleanup | Stop remaining recovery processes and run elevated `restore` with the exact same state directory. Do not delete the journal first. |
| SSH key warning after reinstall | Verify the new host fingerprint on the physical console, then update the trusted entry/re-pair Sync. No automatic trust bypass is provided. |
| Installer quiet/error screen | A transfer log is not installer completion evidence. Inspect the GX10 console. Do not power-cycle a potentially writing installer without diagnosing its state. |

To reload a changed GRUB configuration without rebooting, press `c` at the menu and enter (replace the server IP):

```text
configfile (tftp,172.31.250.1)/grub/grub.cfg
```

This only reloads the menu. STOP remains default; a generated approved submenu still requires the explicit confirm selection.
