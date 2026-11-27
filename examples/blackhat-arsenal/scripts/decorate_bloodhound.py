#!/usr/bin/env python3
"""Add presentation overlays to an authentic BloodHound graph capture.

The graph itself is never redrawn. This script only composites labels, group
markers, and an optional path highlight over the screenshot captured from
BloodHound CE. It also writes transparent SVG overlays that contain no embedded
raster image, so they can be placed over the clean screenshot in slide tools.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageEnhance, ImageFont


WIDTH = 3840
HEIGHT = 2160

FONT_REGULAR = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
FONT_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

GOLD = (251, 191, 36, 255)
BLUE = (96, 165, 250, 255)
PURPLE = (192, 132, 252, 255)
ORANGE = (251, 146, 60, 255)
WHITE = (248, 250, 252, 255)
MUTED = (203, 213, 225, 255)
PANEL = (9, 9, 11, 220)

# Coordinates are for the checked-in 3840 x 2160 BloodHound canvas capture.
GROUP_LABELS = (
    ("INPUT SOURCES", 250, 430, GOLD),
    ("AGENT", 1135, 1110, ORANGE),
    ("ERROR INVESTIGATION", 1870, 48, PURPLE),
    ("BROWSER TESTING", 1870, 430, PURPLE),
    ("DATABASE INVESTIGATION", 1870, 1120, PURPLE),
    ("LOCAL ACCESS", 1870, 1550, BLUE),
    ("ASSETS", 3270, 1610, ORANGE),
)

PATH_NODES = (
    ("1", "CLAUDE.md", (446, 692)),
    ("2", "Claude Code", (1288, 1336)),
    ("3", "Read", (2134, 1750)),
    ("4", ".env", (2978, 1736)),
)
PATH_POINTS = tuple(point for _, _, point in PATH_NODES)


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    if not path.exists():
        raise FileNotFoundError(f"required font not found: {path}")
    return ImageFont.truetype(str(path), size=size)


def _rounded_label(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    color: tuple[int, int, int, int],
) -> None:
    font = _font(FONT_BOLD, 30)
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0] + 52
    height = 58
    draw.rounded_rectangle(
        (x, y, x + width, y + height),
        radius=29,
        fill=PANEL,
        outline=color,
        width=3,
    )
    draw.text((x + 26, y + 10), text, font=font, fill=WHITE)


def _title(draw: ImageDraw.ImageDraw, *, path_view: bool = False) -> None:
    title_font = _font(FONT_BOLD, 58)
    subtitle_font = _font(FONT_REGULAR, 30)
    title = "Credential access path" if path_view else "Agent-Hound representative environment"
    subtitle = (
        "Actual BloodHound CE output — highlighted path: CLAUDE.md → Claude Code → Read → .env"
        if path_view
        else "Actual BloodHound CE output — 19 selected nodes / 20 relationships"
    )
    draw.text((150, 105), title, font=title_font, fill=WHITE)
    draw.text((150, 185), subtitle, font=subtitle_font, fill=MUTED)


def render_overview(clean: Image.Image, destination: Path) -> None:
    output = clean.convert("RGBA")
    draw = ImageDraw.Draw(output, "RGBA")
    _title(draw)
    for text, x, y, color in GROUP_LABELS:
        _rounded_label(draw, text, x, y, color)
    output.convert("RGB").save(destination, quality=95)


def render_path(clean: Image.Image, destination: Path) -> None:
    original = clean.convert("RGBA")
    dimmed = ImageEnhance.Brightness(original.convert("RGB")).enhance(0.34).convert("RGBA")

    reveal = Image.new("L", original.size, 0)
    reveal_draw = ImageDraw.Draw(reveal)
    reveal_draw.line(PATH_POINTS, fill=255, width=150, joint="curve")
    for _, _, (x, y) in PATH_NODES:
        reveal_draw.ellipse((x - 125, y - 125, x + 125, y + 125), fill=255)

    output = Image.composite(original, dimmed, reveal)
    draw = ImageDraw.Draw(output, "RGBA")
    _title(draw, path_view=True)
    number_font = _font(FONT_BOLD, 28)
    for number, _, (x, y) in PATH_NODES:
        draw.ellipse((x - 82, y - 82, x + 82, y + 82), outline=GOLD, width=8)
        badge = (x - 116, y - 114, x - 62, y - 60)
        draw.ellipse(badge, fill=GOLD)
        bbox = draw.textbbox((0, 0), number, font=number_font)
        draw.text(
            (
                badge[0] + (badge[2] - badge[0] - (bbox[2] - bbox[0])) / 2,
                badge[1] + (badge[3] - badge[1] - (bbox[3] - bbox[1])) / 2 - 3,
            ),
            number,
            font=number_font,
            fill=(9, 9, 11, 255),
        )

    output.convert("RGB").save(destination, quality=95)


def _svg_label(text: str, x: int, y: int, color: tuple[int, int, int, int]) -> str:
    width = max(235, 20 * len(text) + 58)
    rgb = f"rgb({color[0]},{color[1]},{color[2]})"
    return (
        f'<g><rect x="{x}" y="{y}" width="{width}" height="58" rx="29" '
        f'fill="#09090b" fill-opacity=".86" stroke="{rgb}" stroke-width="3"/>'
        f'<text x="{x + 26}" y="{y + 39}" fill="#f8fafc" '
        f'font-family="DejaVu Sans, sans-serif" font-size="30" font-weight="700">'
        f'{escape(text)}</text></g>'
    )


def write_overview_overlay(destination: Path) -> None:
    labels = "\n".join(_svg_label(text, x, y, color) for text, x, y, color in GROUP_LABELS)
    destination.write_text(
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">
  <title>Agent-Hound BloodHound presentation overlay</title>
  <desc>Transparent labels for an authentic BloodHound graph screenshot; no graph nodes or raster images are included.</desc>
  <text x="150" y="158" fill="#f8fafc" font-family="DejaVu Sans, sans-serif" font-size="58" font-weight="700">Agent-Hound representative environment</text>
  <text x="150" y="215" fill="#cbd5e1" font-family="DejaVu Sans, sans-serif" font-size="30">Actual BloodHound CE output — 19 selected nodes / 20 relationships</text>
  {labels}
</svg>
''',
        encoding="utf-8",
    )


def write_path_overlay(destination: Path) -> None:
    rings = []
    for number, _, (x, y) in PATH_NODES:
        rings.append(
            f'<circle cx="{x}" cy="{y}" r="82" fill="none" stroke="#fbbf24" stroke-width="8"/>'
            f'<circle cx="{x - 89}" cy="{y - 87}" r="27" fill="#fbbf24"/>'
            f'<text x="{x - 89}" y="{y - 77}" fill="#09090b" text-anchor="middle" '
            f'font-family="DejaVu Sans, sans-serif" font-size="28" font-weight="700">{number}</text>'
        )
    destination.write_text(
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">
  <title>Agent-Hound BloodHound credential-path overlay</title>
  <desc>Transparent path highlight for an authentic BloodHound graph screenshot; no graph nodes or raster images are included.</desc>
  <text x="150" y="158" fill="#f8fafc" font-family="DejaVu Sans, sans-serif" font-size="58" font-weight="700">Credential access path</text>
  <text x="150" y="215" fill="#cbd5e1" font-family="DejaVu Sans, sans-serif" font-size="30">Actual BloodHound CE output — CLAUDE.md → Claude Code → Read → .env</text>
  {''.join(rings)}
</svg>
''',
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="3840x2160 BloodHound graph capture")
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    with Image.open(args.source) as image:
        clean = image.convert("RGB")
    if clean.size != (WIDTH, HEIGHT):
        raise ValueError(f"expected {WIDTH}x{HEIGHT}, got {clean.size[0]}x{clean.size[1]}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    render_overview(clean, args.output_dir / "agenthound-bloodhound-overview.png")
    render_path(clean, args.output_dir / "agenthound-bloodhound-path.png")
    write_overview_overlay(args.output_dir / "agenthound-bloodhound-overlay.svg")
    write_path_overlay(args.output_dir / "agenthound-bloodhound-path-overlay.svg")


if __name__ == "__main__":
    main()
