# Third-party components

The MKD application license applies to MKD's code only. It does not restrict rights granted by the licenses of dependencies, Python, Qt, or vendor media.

Desktop builds use PySide6 Essentials / Shiboken6 and Qt Core, Gui and Widgets, distributed under their applicable LGPLv3 (or alternative upstream) terms. The application uses dynamically loaded, replaceable Qt libraries in an onedir bundle. You may replace or modify those components and reverse engineer this application as needed to debug your modifications to LGPL-covered libraries. The MKD license does not override those rights.

Corresponding Qt/PySide source and license texts are available at:

- https://download.qt.io/official_releases/QtForPython/
- https://code.qt.io/cgit/pyside/pyside-setup.git/
- https://code.qt.io/cgit/qt/
- https://www.gnu.org/licenses/lgpl-3.0.html

The installed package metadata directories accompanying the application contain upstream license texts and exact versions for PySide6 Essentials, Shiboken6, pycdlib and zstandard. Qt's upstream license files are retained by the packaging hook. Python's bundled license and the PyInstaller bootloader notices are retained in the distribution. Consult those licenses for their individual permissions and obligations.

- Python: PSF license — https://docs.python.org/3/license.html
- pycdlib: LGPL-2.1 — https://github.com/clalancette/pycdlib
- python-zstandard: BSD — https://github.com/indygreg/python-zstandard
- PyInstaller bootloader: GPL with an exception permitting bundled applications — https://pyinstaller.org/en/stable/license.html

ASUS OS images and Canonical EFI loaders are downloaded directly from the vendor by the user, under their own terms. They are not included in this installer. MKD is not affiliated with NVIDIA, ASUS, Canonical, or The Qt Company.
