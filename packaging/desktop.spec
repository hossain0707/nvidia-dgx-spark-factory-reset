# PyInstaller onedir keeps Qt libraries replaceable; no vendor recovery image is bundled.
from pathlib import Path
import sys
from importlib.metadata import version
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH).parent
datas = [(str(root / 'LICENSE'), '.'), (str(root / 'THIRD_PARTY_NOTICES.md'), '.'),
         (str(root / 'docs'), 'docs'),
         (str(root / 'src/spark_recovery/target.py'), 'spark_recovery')]
datas += [(str(root / 'packaging/licenses'), 'licenses')]
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
if python_license.exists():
    datas += [(str(python_license), 'licenses/Python')]
for distribution in ('PySide6_Essentials', 'shiboken6', 'pycdlib', 'zstandard', 'pyinstaller', 'paramiko',
                     'cryptography', 'bcrypt', 'pynacl', 'cffi', 'pycparser', 'invoke'):
    datas += copy_metadata(distribution)
a = Analysis([str(root / 'desktop_entry.py')], pathex=[str(root / 'src')], datas=datas,
             module_collection_mode={'paramiko': 'py'},
             hiddenimports=['pycdlib', 'zstandard'], excludes=['tkinter', 'PySide6.QtNetwork',
                            'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtWebEngineCore'])
pyz = PYZ(a.pure)
icon = root / 'build/icons' / ('MKD.ico' if sys.platform == 'win32' else 'MKD.icns')
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MKD-Spark-Recovery', icon=str(icon) if icon.exists() else None,
          console=False, debug=False, strip=False, upx=False)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='MKD-Spark-Recovery')
if sys.platform == 'darwin':
    bundle = BUNDLE(collection, name='MKD Spark Recovery.app', icon=str(icon) if icon.exists() else None,
                    bundle_identifier='com.mkd.spark-recovery',
                    info_plist={'CFBundleShortVersionString': version('spark-recovery'), 'CFBundleVersion': version('spark-recovery'),
                                'NSHighResolutionCapable': True,
                                'NSLocalNetworkUsageDescription': 'Discover and recover your device over the selected dedicated Ethernet link.',
                                'NSHumanReadableCopyright': 'Copyright 2026 MKD. See LICENSE.'})
