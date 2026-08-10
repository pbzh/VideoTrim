"""Generate the VideoTrim app icon (icon.png + icon.ico + icon.icns) from icon.svg.

Run: python assets/make_icon.py

Source of truth is icon.svg (dark-slate rounded tile, gradient timeline frame
with trim cut-lines, play triangle, corner crop brackets, "00:15" timecode).
The SVG is rasterized to a 1024px PNG, then downscaled into the platform icons.

Rasterizer: tries rsvg-convert, then Inkscape, then cairosvg, then macOS
qlmanage (Quick Look) — first one found wins.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
SVG = HERE / "icon.svg"
RASTER = 1024


def rasterize_svg(svg: Path, out_png: Path, size: int) -> None:
    """Render *svg* to a *size*x*size* PNG at *out_png* using whatever
    SVG rasterizer is on the system."""
    if shutil.which("rsvg-convert"):
        subprocess.run(
            ["rsvg-convert", "-w", str(size), "-h", str(size), "-o", str(out_png), str(svg)],
            check=True,
        )
        return
    if shutil.which("inkscape"):
        subprocess.run(
            ["inkscape", str(svg), "--export-type=png", f"--export-filename={out_png}",
             "-w", str(size), "-h", str(size)],
            check=True,
        )
        return
    try:
        import cairosvg  # type: ignore
        cairosvg.svg2png(url=str(svg), write_to=str(out_png),
                         output_width=size, output_height=size)
        return
    except ImportError:
        pass
    if shutil.which("qlmanage"):  # macOS Quick Look fallback
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                ["qlmanage", "-t", "-s", str(size), "-o", tmp, str(svg)],
                check=True, capture_output=True,
            )
            thumb = next(Path(tmp).glob("*.png"))
            Image.open(thumb).save(out_png)
        return
    raise RuntimeError(
        "No SVG rasterizer found. Install one of: rsvg-convert, inkscape, "
        "cairosvg (pip install cairosvg), or run on macOS (qlmanage)."
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        raster = Path(tmp) / "icon.png"
        rasterize_svg(SVG, raster, RASTER)
        src = Image.open(raster).convert("RGBA")

    base = src.resize((256, 256), Image.LANCZOS)
    base.save(HERE / "icon.png")

    sizes = [16, 24, 32, 48, 64, 128, 256]
    base.save(HERE / "icon.ico", sizes=[(s, s) for s in sizes])
    wrote = ["icon.png", "icon.ico"]

    # macOS .icns — PIL needs a 1024px source.
    try:
        src.resize((1024, 1024), Image.LANCZOS).save(HERE / "icon.icns")
        wrote.append("icon.icns")
    except Exception as e:  # pragma: no cover - platform/format dependent
        print(f"Skipped icon.icns: {e}")

    print("Wrote " + ", ".join(str(HERE / w) for w in wrote))


if __name__ == "__main__":
    main()
