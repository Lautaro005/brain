"""Genera src-tauri/icons/* a partir del ícono de brain (el «fichero» de la landing). Uso: python3 assets/make_icons.py"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "src-tauri" / "icons"
S = 1024


K = 664 / 26  # el mismo dibujo que el logo de la web (viewBox 32x32): una ficha con un grafo adentro


def u(x: float) -> float:
    return 180 + (x - 3) * K


def v(y: float) -> float:
    return 205 + (y - 4) * K


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ink, paper = (10, 10, 10, 255), (255, 255, 255, 255)
    d.rounded_rectangle((40, 40, S - 40, S - 40), radius=210, fill=ink)
    # la ficha: tarjeta blanca con la franja del encabezado
    d.rounded_rectangle((u(3), v(4), u(29), v(28)), radius=3 * K, fill=paper)
    d.rectangle((u(3), v(9.6), u(29), v(11.4)), fill=ink)
    # el grafo: un nodo central unido a tres más
    c, sats = (16, 20), [(9.5, 16), (22.5, 15.5), (20.5, 24)]
    for x, y in sats:
        d.line((u(c[0]), v(c[1]), u(x), v(y)), fill=ink, width=int(1.9 * K))
    for (x, y), r in [(c, 2.9)] + [(p, 2.1) for p in sats]:
        d.ellipse((u(x) - r * K, v(y) - r * K, u(x) + r * K, v(y) + r * K), fill=ink)
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
