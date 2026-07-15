from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter


def main() -> None:
    application = QGuiApplication.instance() or QGuiApplication([])
    target = Path(__file__).resolve().parents[1] / "assets" / "EverandEPUBStudio.ico"
    target.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#111827"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(8, 8, 240, 240, 52, 52)
    painter.setBrush(QColor("#f59e0b"))
    painter.drawRoundedRect(28, 28, 200, 200, 38, 38)
    painter.setPen(QColor("#111827"))
    painter.setFont(QFont("Segoe UI", 126, QFont.Weight.Black))
    painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, "E")
    painter.end()
    if not image.save(str(target), "ICO"):
        raise SystemExit("Não foi possível gerar o ícone ICO.")
    application.processEvents()
    print(target)


if __name__ == "__main__":
    main()
