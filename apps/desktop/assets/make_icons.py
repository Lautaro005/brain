"""Genera src-tauri/icons/* a partir del ícono de brain (el «fichero» de la landing). Uso: python3 assets/make_icons.py"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "src-tauri" / "icons"
S = 1024


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((40, 40, S - 40, S - 40), radius=210, fill=(10, 10, 10, 255))
    # el fichero: tarjeta blanca con encabezado y dos renglones
    x0, y0, x1, y1 = 180, 270, 844, 754
    d.rounded_rectangle((x0, y0, x1, y1), radius=40, fill=(255, 255, 255, 255))
    d.rectangle((x0, y0 + 120, x1, y0 + 132), fill=(10, 10, 10, 255))
    d.rounded_rectangle((x0 + 70, y0 + 230, x0 + 420, y0 + 262), radius=16, fill=(10, 10, 10, 255))
    d.rounded_rectangle((x0 + 70, y0 + 320, x0 + 300, y0 + 352), radius=16, fill=(10, 10, 10, 255))
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw()
    for name, size in (("32x32.png", 32), ("128x128.png", 128), ("128x128@2x.png", 256), ("icon.png", 512)):
        big.resize((size, size), Image.LANCZOS).save(OUT / name)
    big.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.save(OUT / "icon.icns")


if __name__ == "__main__":
    main()
