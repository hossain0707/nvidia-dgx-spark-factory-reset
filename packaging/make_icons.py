"""Generate original MKD vector-style app icons without external artwork."""
from pathlib import Path
import subprocess
import sys

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPolygonF

app = QGuiApplication([])
root = Path(__file__).resolve().parents[1]
folder = root / "build" / "icons"
folder.mkdir(parents=True, exist_ok=True)
iconset = folder / "MKD.iconset"
iconset.mkdir(exist_ok=True)
for size in (16, 32, 64, 128, 256, 512, 1024):
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(QColor("#0a1220"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 100, size / 100)
    painter.setPen(QPen(QColor("#66e1b9"), 8))
    painter.drawPolyline(QPolygonF([QPointF(22, 72), QPointF(22, 28), QPointF(50, 55),
                                   QPointF(78, 28), QPointF(78, 72)]))
    painter.end()
    if size == 256 and sys.platform == "win32":
        assert image.save(str(folder / "MKD.ico"), "ICO")
    if size <= 512:
        assert image.save(str(iconset / f"icon_{size}x{size}.png"))
    if size >= 32:
        half = size // 2
        assert image.save(str(iconset / f"icon_{half}x{half}@2x.png"))
if sys.platform == "darwin":
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(folder / "MKD.icns")], check=True)
