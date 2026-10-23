# Linux host

Use a current Linux distribution with Python 3.11+, `iproute2`, Git, and a dedicated Ethernet interface. Install your distribution's Python venv package if `python3 -m venv` is unavailable. Full packaged Linux-host recovery has not yet been hardware-qualified; CI and mocked network-adapter checks are not substitutes for diagnostic boot.

```sh
ip -br link
ip -br address
sh run.sh wizard
sh run.sh prepare --download
sh run.sh inspect-target --ssh operator@TARGET_ADDRESS
sh run.sh doctor
```

Use the actual Ethernet interface name. NetworkManager can retain its existing configuration: managed mode adds an address using `ip address add`, records it, and removes only that address afterward. It does not replace routes or edit permanent NetworkManager files. A network manager that removes the address during recovery must be configured by the operator before retrying.

## Firewall and DHCP

Stop or separately reconfigure any DHCP server bound to the chosen link before serving. The tool refuses an occupied UDP 67; it never silently kills dnsmasq, systemd-networkd or another network manager.

Allow recovery traffic on the dedicated interface: DHCP UDP 67 from an unconfigured client, target-to-host HTTP TCP 8080 (or your configured port), and target-to-host TFTP UDP including ephemeral transfer ports. For UFW, the following are **templates** to adapt and review, not literal commands with angle brackets:

```text
sudo ufw allow in on INTERFACE to any port 67 proto udp
sudo ufw allow in on INTERFACE from TARGET_IP to HOST_IP proto udp
sudo ufw allow in on INTERFACE from TARGET_IP to HOST_IP port 8080 proto tcp
```

Do not disable the firewall globally or apply rules on a shared office network. Remove only the exact temporary rules you added after recovery (`ufw delete` with the same rule). Managed nftables/firewalld policies require equivalent scoped rules from their administrator; this release does not edit them automatically.

## Serve without idle sleep

```sh
sudo systemd-inhibit --what=sleep --why="GX10 Ethernet recovery" \
  .venv/bin/python -m spark_recovery serve --manage-network
```

On a non-systemd host, manage sleep settings yourself and run the same Python command with sudo. No local process can guarantee protection against forced sleep, power loss or unplugged Ethernet. Follow the [runbook](RECOVERY.md) and verify diagnostic mode before installing.

## Cleanup

Ctrl+C restores the journaled interface alias. After an abnormal exit, first stop all recovery processes, then:

```sh
sudo .venv/bin/python -m spark_recovery restore
```

Remove any manually added firewall rules separately. Do not delete the state journal before successful restoration.
