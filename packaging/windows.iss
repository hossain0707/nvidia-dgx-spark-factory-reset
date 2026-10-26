[Setup]
AppId={{6980138A-1AE8-4EF1-912D-5EA4866307D6}
AppName=MKD Spark Recovery
AppVersion=0.2.0
AppPublisher=MKD
AppPublisherURL=https://github.com/mkd-hossain/nvidia-dgx-spark-factory-reset
DefaultDirName={autopf}\MKD Spark Recovery
DefaultGroupName=MKD Spark Recovery
OutputDir=..\dist\installers
OutputBaseFilename=MKD-Spark-Recovery-0.2.0-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
LicenseFile=..\LICENSE
UninstallDisplayIcon={app}\MKD-Spark-Recovery.exe

[Files]
Source: "..\dist\MKD-Spark-Recovery\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\MKD Spark Recovery"; Filename: "{app}\MKD-Spark-Recovery.exe"
Name: "{autodesktop}\MKD Spark Recovery"; Filename: "{app}\MKD-Spark-Recovery.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\MKD-Spark-Recovery.exe"; Description: "Open MKD Spark Recovery"; Flags: nowait postinstall skipifsilent runasoriginaluser
