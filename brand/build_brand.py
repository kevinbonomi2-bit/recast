"""Write Recast's logo masters (SVG) and the Windows icon (recast.ico) into this folder.

The geometry lives in recast/ui/logo.py so the app draws the very same mark at runtime.
Run:  .venv\\Scripts\\python brand\\build_brand.py
"""

import io
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from recast.ui import logo  # noqa: E402

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def to_pil(img) -> Image.Image:
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841
    for name, text in logo.MASTERS.items():
        (HERE / name).write_text(text, encoding="utf-8")
    # Each icon size is drawn on its own: the small cut below 32 px, the full mark above.
    frames = [to_pil(logo.render(logo.MASTERS["recast-app-icon-small.svg" if px <= 24 else "recast-app-icon.svg"], px))
              for px in ICO_SIZES]
    frames[-1].save(HERE / "recast.ico", sizes=[(px, px) for px in ICO_SIZES], append_images=frames[:-1])
    to_pil(logo.render(logo.MASTERS["recast-app-icon.svg"], 512)).save(HERE / "recast-app-icon-512.png")
    to_pil(logo.render(logo.MASTERS["recast-mark.svg"], 512)).save(HERE / "recast-mark-512.png")
    print("wrote", ", ".join(sorted(p.name for p in HERE.iterdir() if p.suffix in (".svg", ".ico", ".png"))))


if __name__ == "__main__":
    main()
