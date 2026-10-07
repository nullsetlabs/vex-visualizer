"""Share images (Open Graph, 1200 x 630) for the VEX site.

Writes an SVG source and renders a PNG with headless Chrome for:
  visualizer/og-visualizer.svg/.png   the season tracker and its standalone pages
  og-vex.svg/.png                     the VEX home page at the repository root

Layout follows the Null Set Labs project cards (brand-assets/social/og-*.svg in
the website folder): lab mark, research area in small capitals, title, italic
description, address line. The tracker card adds the tracker's section colors.
New season: change SEASON below and run

    python visualizer/dev/make_og_images.py

The Worlds 2026 archive keeps its own visualizer/og-preview.png; never overwrite it.
"""
import html
import os
import shutil
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SEASON = "V5RC Override 2026-2027"
CHROME = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
          r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
          "google-chrome", "chromium"]
FONTS = ("https://fonts.googleapis.com/css2?family=Inter:wght@500;600&family=JetBrains+Mono:wght@500"
         "&family=Newsreader:ital,opsz,wght@0,6..72,500;1,6..72,400&display=block")

# Tracker section colors (same tokens as visualizer/index.html).
CHIPS = [("Skills leaderboard", "#fb923c"), ("Signature events", "#fbbf24"),
         ("TrueSkill rankings", "#a3e635"), ("Team pages", "#60a5fa")]


def card(label, title, lines, footer, chips=(), title_size=88):
    e = html.escape
    y_lines = 318 if chips else 340
    body = [f'<text x="600" y="{y_lines + 42 * i}" text-anchor="middle" fill="#8a8a92" '
            f"font-family=\"'Newsreader', Georgia, serif\" font-size=\"28\" font-style=\"italic\" "
            f'font-weight="400">{e(t)}</text>' for i, t in enumerate(lines)]
    if chips:
        w, gap, h, top = 250, 14, 56, 390
        x = (1200 - (len(chips) * w + (len(chips) - 1) * gap)) / 2
        for name, color in chips:
            body.append(f'<rect x="{x:.0f}" y="{top}" width="{w}" height="{h}" rx="28" fill="#111827" '
                        f'stroke="{color}" stroke-opacity="0.6" stroke-width="2"/>'
                        f'<circle cx="{x + 30:.0f}" cy="{top + h / 2:.0f}" r="7" fill="{color}"/>'
                        f'<text x="{x + 48:.0f}" y="{top + 35}" fill="#ededeb" font-family="Inter, system-ui, sans-serif" '
                        f'font-size="20" font-weight="600">{e(name)}</text>')
            x += w + gap
        body.append('<text x="600" y="500" text-anchor="middle" fill="#8a8a92" '
                    "font-family=\"'Newsreader', Georgia, serif\" font-size=\"24\" font-style=\"italic\" "
                    'font-weight="400">Official VEX results, updated every 30 minutes during signature events.</text>')
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630" width="1200" height="630" role="img" aria-label="{e(label)}">
  <defs>
    <linearGradient id="bgGradient" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0c0c14"/>
      <stop offset="100%" stop-color="#0a0a0f"/>
    </linearGradient>
    <linearGradient id="slashGradient" x1="15%" y1="85%" x2="85%" y2="15%">
      <stop offset="0%" stop-color="#3b82f6"/>
      <stop offset="50%" stop-color="#7d7a7a"/>
      <stop offset="100%" stop-color="#c19a5b"/>
    </linearGradient>
    <radialGradient id="glow" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#c19a5b" stop-opacity="0.14"/>
      <stop offset="100%" stop-color="#c19a5b" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="1200" height="630" fill="url(#bgGradient)"/>
  <circle cx="600" cy="170" r="240" fill="url(#glow)"/>
  <g transform="translate(560 50)">
    <circle cx="40" cy="40" r="30" fill="none" stroke="#c19a5b" stroke-width="3"/>
    <line x1="16" y1="64" x2="64" y2="16" stroke="url(#slashGradient)" stroke-width="6" stroke-linecap="round"/>
  </g>
  <text x="600" y="170" text-anchor="middle" fill="#c19a5b" font-family="'JetBrains Mono', monospace" font-size="20" font-weight="500" letter-spacing="5">ROBOTICS</text>
  <text x="600" y="262" text-anchor="middle" fill="#ededeb" font-family="'Newsreader', Georgia, serif" font-size="{title_size}" font-weight="500" letter-spacing="-1">{e(title)}</text>
  {chr(10).join('  ' + b for b in body).strip()}
  <text x="600" y="575" text-anchor="middle" fill="#5e5e66" font-family="'JetBrains Mono', monospace" font-size="16" font-weight="500" letter-spacing="3">{e(footer)}</text>
</svg>
"""


def render(svg_path, png_path):
    exe = next((c for c in CHROME if shutil.which(c) or os.path.exists(c)), None)
    if not exe:
        raise SystemExit("Chrome or Edge not found; the SVG is written, render it by hand.")
    with tempfile.TemporaryDirectory() as tmp:
        page = os.path.join(tmp, "card.html")
        with open(svg_path, encoding="utf-8") as f:
            svg = f.read().split("?>", 1)[1]
        with open(page, "w", encoding="utf-8") as f:
            f.write(f'<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="{FONTS}">'
                    f"<style>html,body{{margin:0;background:#0a0a0f}}svg{{display:block}}</style></head><body>{svg}</body></html>")
        subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={tmp}\\profile",
                        "--window-size=1200,630", "--virtual-time-budget=8000", f"--screenshot={png_path}",
                        "file:///" + page.replace("\\", "/")], check=True, capture_output=True, timeout=120)


def main():
    jobs = [
        (os.path.join(ROOT, "visualizer", "og-visualizer"),
         card("VEX Visualizer Open Graph preview", "VEX Visualizer", [f"{SEASON} season tracker, by Arjun"],
              "NULL SET LABS  /  vex.nullsetlabs.org/visualizer", CHIPS)),
        (os.path.join(ROOT, "og-vex"),
         card("VEX at Null Set Labs Open Graph preview", "VEX at Null Set Labs",
              [f"The VEX Visualizer by Arjun: a {SEASON} season tracker,",
               "and the Worlds 2026 dashboard archive."],
              "NULL SET LABS  /  vex.nullsetlabs.org", title_size=80)),
    ]
    for base, svg in jobs:
        with open(base + ".svg", "w", encoding="utf-8", newline="\n") as f:
            f.write(svg)
        render(base + ".svg", base + ".png")
        print("wrote", os.path.relpath(base + ".png", ROOT))


if __name__ == "__main__":
    main()
