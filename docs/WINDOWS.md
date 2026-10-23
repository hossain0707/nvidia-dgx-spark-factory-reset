# Windows host

Use Windows 10/11 with an Ethernet adapter and Python 3.11+ on PATH. Read the [runbook](RECOVERY.md) first. The original Windows/GX10 recovery workflow was verified on hardware; the packaged host adapter is tested in CI with network mutations mocked and still needs broader hardware qualification.

## Prepare in normal PowerShell

```powershell
Get-NetAdapter | Select-Object Name, InterfaceIndex, Status, LinkSpeed
.\run.ps1 wizard
.\run.ps1 prepare --download
.\run.ps1 inspect-target --ssh operator@TARGET_ADDRESS
.\run.ps1 doctor
```

Use the **numeric Ethernet InterfaceIndex** in the wizard. Do not select Wi-Fi. A direct link should show Up. The addresses in your session must not overlap another active network. If PowerShell policy blocks a reviewed launcher, use the equivalent commands directly instead of globally changing execution policy:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m spark_recovery wizard
```

## Start in Administrator PowerShell

Open PowerShell as Administrator and change back to the cloned repository directory:

```powershell
.\.venv\Scripts\python.exe -m spark_recovery serve --manage-network --stop-ics
```

`--stop-ics` explicitly permits temporarily stopping Internet Connection Sharing. Without that flag, running ICS is a stop condition. Windows may remove its private Ethernet address when ICS stops; this adapter explicitly retains the selected recovery address. It does not change Wi-Fi/default routes.

The tool creates session-specific Windows Firewall rules scoped to the chosen Ethernet adapter and Python executable:

- TCP HTTP port: only the target IP to the recovery host IP.
- UDP TFTP and its ephemeral transfer ports: only the target IP to the host IP.
- UDP 67: only the dedicated interface; software filters the exact target MAC.

Do not disable Windows Firewall globally. Only the tool's uniquely named rules are removed on cleanup. Firewall/group policy can still reject a rule; failure restores the saved host state and stops the operation.

Use a second terminal for `attest-diagnostic`, `enable-install` and `revoke-install`. Leave the first terminal running until installation completes. The default state folder is relative to the working directory; use the same directory, or specify the same absolute `--state` everywhere.

## Cleanup

Ctrl+C in the serving terminal restores the journaled Ethernet/DHCP/ICS state and removes only that session's firewall rules. For an abnormal exit, after stopping recovery processes:

```powershell
.\.venv\Scripts\python.exe -m spark_recovery restore
```

Run restoration as Administrator. Keep the laptop plugged in, awake, and its lid open while serving. Windows idle-sleep inhibition does not override every lid/power policy.
