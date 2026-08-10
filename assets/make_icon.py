"""Generate the VideoTrim app icon (icon.ico + icon.png).

Run: python assets/make_icon.py
Design: rounded dark-slate tile, accent play triangle flanked by two trim
bars — reads as "trim a video" even at 16x16.
"""
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
SS = 8  # supersample factor for smooth edges
BASE = 256
S = BASE * SS

BG_TOP = (38, 50, 64)      # slate
BG_BOT = (24, 32, 42)
ACCENT = (78, 201, 148)    # green (matches COLOR_SUCCESS in app)
BAR = (236, 240, 244)      # near-white trim bars


def rounded_mask(size: int, radius: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def vertical_gradient(size: int, top, bot) -> Image.Image:
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        grad.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(top, bot)))
    return grad.resize((size, size))


def build() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    bg = vertical_gradient(S, BG_TOP, BG_BOT).convert("RGBA")
    img.paste(bg, (0, 0), rounded_mask(S, int(S * 0.22)))

    d = ImageDraw.Draw(img)

    # Two trim bars
    bar_w = int(S * 0.085)
    bar_top = int(S * 0.28)
    bar_bot = int(S * 0.72)
    left_x = int(S * 0.24)
    right_x = int(S * 0.76) - bar_w
    r = bar_w // 2
    d.rounded_rectangle([left_x, bar_top, left_x + bar_w, bar_bot], radius=r, fill=BAR)
    d.rounded_rectangle([right_x, bar_top, right_x + bar_w, bar_bot], radius=r, fill=BAR)

    # Center play triangle
    cx, cy = S // 2, S // 2
    tw = int(S * 0.17)
    th = int(S * 0.22)
    d.polygon(
        [(cx - tw // 2, cy - th // 2), (cx - tw // 2, cy + th // 2), (cx + tw, cy)],
        fill=ACCENT,
    )

    return img.resize((BASE, BASE), Image.LANCZOS)


def main() -> None:
    icon = build()
    icon.save(HERE / "icon.png")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    icon.save(HERE / "icon.ico", sizes=[(s, s) for s in sizes])
    wrote = ["icon.png", "icon.ico"]

    # macOS .icns — best effort; PIL needs a 1024px source.
    try:
        icon.resize((1024, 1024), Image.LANCZOS).save(HERE / "icon.icns")
        wrote.append("icon.icns")
    except Exception as e:  # pragma: no cover - platform/format dependent
        print(f"Skipped icon.icns: {e}")

    print("Wrote " + ", ".join(str(HERE / w) for w in wrote))


if __name__ == "__main__":
    main()
