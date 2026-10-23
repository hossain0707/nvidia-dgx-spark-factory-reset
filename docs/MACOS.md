# macOS host

Use Python 3.11+, Git and a wired Ethernet adapter. Apple-silicon and Intel hosts share the Python implementation; full GX10 recovery on macOS has **not** been hardware-qualified by this project. CI tests portable protocols and mocked interface operations; test the diagnostic boot before considering installation.

## Identify the adapter and prepare

```sh
networksetup -listallhardwareports
ifconfig
sh run.sh wizard
sh run.sh prepare --download
sh run.sh inspect-target --ssh operator@TARGET_ADDRESS
sh run.sh doctor
```

Use the `Device` belonging to your Ethernet adapter, for example `en5`. Do not choose Wi-Fi. If the bundled Python is too old, install Python 3.11+ from python.org or your trusted package manager. Install dependencies as your ordinary user, not with sudo.

## Serve

```sh
sudo .venv/bin/python -m spark_recovery serve --manage-network
```

Administrator/root privileges are needed for DHCP/TFTP low ports and the interface alias. The adapter adds the chosen recovery IPv4 alias using `ifconfig`, binds outgoing DHCP to that interface, and removes only its added alias on cleanup. It does not turn on Internet Sharing or change the default route.

Disable **Internet Sharing** for this dedicated link before serving if it has its own DHCP service; the tool refuses port conflicts. It never silently kills a competing DHCP server. Allow the selected Python executable to receive incoming traffic in **System Settings → Network → Firewall → Options** when applicable. Do not disable the firewall globally. Custom PF/VPN/managed-firewall policies require their administrator's scoped rules; this release does not modify those policies.

The process uses `caffeinate -i` while serving. Keep the Mac powered and its lid open. This does not protect against cable/power loss or forced sleep. Follow the complete [runbook](RECOVERY.md), including local console confirmation on the GX10.

## Cleanup

Ctrl+C restores managed interface changes. After a crash, stop remaining recovery processes before:

```sh
sudo .venv/bin/python -m spark_recovery restore
```

Do not remove the state directory before restoration. If a managed firewall rule was added manually, remove that exact rule separately; the tool does not disable unrelated rules.
