"""Turn the white-on-black logo into assets/logo.png with a transparent background.

Usage: python tools/make_icon.py <source image>
"""
import sys
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, qGray

OUTPUT = Path(__file__).resolve().parent.parent / "assets" / "logo.png"
SIZE = 512
PADDING = 0.02
BLACK_THRESHOLD = 12


def main(source: str) -> int:
    image = QImage(source).convertToFormat(QImage.Format.Format_ARGB32)
    if image.isNull():
        print(f"Image illisible : {source}")
        return 1
    # Brightness becomes opacity, so the logo keeps its antialiased edges.
    left, top, right, bottom = image.width(), image.height(), 0, 0
    for y in range(image.height()):
        for x in range(image.width()):
            alpha = qGray(image.pixel(x, y))
            alpha = 0 if alpha < BLACK_THRESHOLD else alpha
            image.setPixelColor(x, y, QColor(255, 255, 255, alpha))
            if alpha:
                left, right = min(left, x), max(right, x)
                top, bottom = min(top, y), max(bottom, y)

    side = max(right - left, bottom - top) + 1
    side += int(side * PADDING) * 2
    crop = QRect((left + right - side) // 2, (top + bottom - side) // 2, side, side)
    result = image.copy(crop).scaled(
        SIZE, SIZE, Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    OUTPUT.parent.mkdir(exist_ok=True)
    result.save(str(OUTPUT))
    print(f"{OUTPUT} ({SIZE}x{SIZE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
