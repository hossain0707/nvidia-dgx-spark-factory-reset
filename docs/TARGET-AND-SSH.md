# Local GX10 and remote SSH workflows

A GX10 cannot recover itself through PXE without a **separate recovery host**. Target-local commands and SSH automate inventory and one-time boot selection; the host still serves DHCP/TFTP/HTTP over Ethernet. A GX10 monitor/keyboard is required for diagnostic verification and erase-menu confirmation. Fully headless unattended reinstall is outside this release's scope.

## Local terminal on the GX10

Clone the repository on the GX10. The target helper uses only Python's standard library and Linux utilities; image-extraction dependencies are not required for this standalone path:

```sh
python3 src/spark_recovery/target.py --json > target-inventory.json
```

Review it, then transfer the JSON to the recovery host using your existing trusted file-transfer route. On the host:

```sh
python -m spark_recovery inspect-target --file target-inventory.json
```

Only after the other host is prepared and serving, and after handling active workloads, select one-time PXE boot locally:

```sh
sudo python3 src/spark_recovery/target.py --mac YOUR_ETHERNET_MAC --apply
```

That sets `BootNext` but does not reboot. To request both explicitly:

```sh
sudo python3 src/spark_recovery/target.py --mac YOUR_ETHERNET_MAC --apply --reboot
```

The helper detects the unique IPv4 PXE entry matching this NIC's MAC from `efibootmgr -v`. It does **not** assume `0001` is correct on every device. It refuses non-ARM64 targets, missing MACs and ambiguous entries. BootNext is one-time, not a permanent boot-order change. A successful reboot is not proof that recovery or installation completed.

## From a remote Windows/Mac/Linux terminal

First establish and verify ordinary SSH access yourself. The tool uses your native `ssh` executable and existing SSH configuration, with `StrictHostKeyChecking=yes`. It never collects passwords or rewrites `known_hosts`.

```sh
python -m spark_recovery inspect-target --ssh operator@TARGET_ADDRESS
```

For a remote BootNext/reboot, the account must already have appropriate **noninteractive sudo** authorization. This project does not install a broad sudo rule or ask you to share a password:

```sh
python -m spark_recovery ssh-boot --ssh operator@TARGET_ADDRESS --apply
python -m spark_recovery ssh-boot --ssh operator@TARGET_ADDRESS --apply --reboot
```

The target helper is transmitted inside the existing SSH channel as Python source. No agent is installed, no private key is copied, and there is no shell execution of inventory values. If sudo or firmware access is denied, use the local terminal or firmware menu; do not weaken SSH trust checks to proceed.

## After reinstall

The old account/key pairing may no longer exist and the host key normally changes. On the physical GX10, inspect the new fingerprint (for example `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`). Compare it with the remote connection before updating a known-host entry or pairing NVIDIA Sync again. Never blindly bypass a host-key warning. Choose the desired username and hostname during first-run setup; this recovery project deliberately does not include account-renaming scripts.
