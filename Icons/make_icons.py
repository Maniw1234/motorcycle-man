# Draws the Motorcycle Man logo (its black glitter helmet with the eyes lit on a blue visor, edged in
# light so it shows on dark backgrounds too: the same helmet as on the phone and the PC rider, from
# Windows/helmet_face.py) and writes every icon the apps use:
#   Icons/motorcycle-man-logo.png        1024 px master
#   Windows/motorcycle_man.ico           Windows program and window icon
#   Windows/motorcycle_man_icon.png      phone page icon (browser tab)
#   Windows/motorcycle_man_app_icon.png  the phone page on a home screen: the helmet on a night-blue glow
#   Android/res/...                      Android launcher icons: the helmet on the same glow
# Run from anywhere: python Icons/make_icons.py
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QRadialGradient
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Windows"))
import helmet_face  # noqa: E402

SIZE = 1024
GLOW = ((0.0, "#2A3B86"), (0.55, "#141C44"), (1.0, "#0B1026"))  # the Android icon's background, middle to edge


def to_pil(image: QImage) -> Image.Image:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def draw_logo():
    app = QGuiApplication.instance() or QGuiApplication([])  # Qt needs it to draw the gradients
    image = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for svg in (helmet_face.outline_svg(width=7), helmet_face.face_svg("idle")):
        QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, SIZE, SIZE))
    painter.end()
    logo = to_pil(image)
    del app
    return logo


def draw_glow(size):
    app = QGuiApplication.instance() or QGuiApplication([])
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    glow = QRadialGradient(QPointF(size / 2, size * 0.46), size * 0.62)
    for at, colour in GLOW:
        glow.setColorAt(at, QColor(colour))
    painter.fillRect(image.rect(), glow)
    painter.end()
    picture = to_pil(image)
    del app
    return picture


def scaled(img, size, content=1.0):
    # The logo scaled to `size`, shrunk to `content` of the canvas and centered.
    inner = max(1, round(size * content))
    small = img.resize((inner, inner), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(small, ((size - inner) // 2, (size - inner) // 2), small)
    return canvas


def badge(logo, size):
    # For Androids older than 8, which show the icon as it is: the helmet on the glow, in a circle.
    big = size * 4
    canvas = draw_glow(big)
    canvas.alpha_composite(scaled(logo, big, 0.74))
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    canvas.putalpha(mask)
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def main():
    logo = draw_logo()
    logo.save(ROOT / "Icons" / "motorcycle-man-logo.png")
    logo.save(ROOT / "Windows" / "motorcycle_man.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    scaled(logo, 192).save(ROOT / "Windows" / "motorcycle_man_icon.png")
    # Phones may crop a home-screen icon to a circle that leaves out its outer tenth, so the helmet stays inside.
    app_icon = draw_glow(512)
    app_icon.alpha_composite(scaled(logo, 512, 0.62))
    app_icon.save(ROOT / "Windows" / "motorcycle_man_app_icon.png")

    res = ROOT / "Android" / "res"
    densities = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}
    for name, scale in densities.items():
        folder = res / f"mipmap-{name}"
        folder.mkdir(parents=True, exist_ok=True)
        badge(logo, round(48 * scale)).save(folder / "ic_launcher.png")
        # Adaptive icons are 108 dp with a 66 dp safe circle; the helmet is a little taller than wide.
        scaled(logo, round(108 * scale), 0.58).save(folder / "ic_launcher_foreground.png")
        draw_glow(round(108 * scale)).save(folder / "ic_launcher_background.png")
    (res / "mipmap-anydpi-v26").mkdir(parents=True, exist_ok=True)
    (res / "mipmap-anydpi-v26" / "ic_launcher.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@mipmap/ic_launcher_background" />\n'
        '    <foreground android:drawable="@mipmap/ic_launcher_foreground" />\n'
        '</adaptive-icon>\n', encoding="utf-8")
    colors = res / "values" / "colors.xml"
    if colors.exists():
        colors.unlink()  # the background was a plain colour; it is the glow picture now
    print("Wrote the logo and all icons.")


if __name__ == "__main__":
    main()
