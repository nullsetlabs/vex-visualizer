"""
Apply the Null Set Labs context strip to a VEX Visualizer HTML file.

This script inserts a thin "Null Set Labs" navigation strip at the very top
of the page, above the existing visualizer header. The strip lets visitors
navigate back to nullsetlabs.org and to the Robotics pillar.

Idempotent: if the strip is already present, the script reports and exits
without changing the file.

Usage:
    python apply_context_strip.py <path_to_html_file>

Example:
    python apply_context_strip.py index.html
    python apply_context_strip.py vex_visualizer_template.html
"""

import sys
import re
from pathlib import Path

MARKER = "<!-- BEGIN Null Set Labs context strip -->"

CSS_BLOCK = """
  /* === Null Set Labs context strip ============================== */
  /* Thin navigation bar above the visualizer header. Brings the
     visualizer back into the Null Set Labs surface so visitors can
     reach the umbrella, the Robotics pillar, and the rest of the lab. */
  .nsl-strip {
    position: sticky;
    top: 0;
    z-index: 200;
    background: #0a0a0f;
    border-bottom: 1px solid #26262f;
    font-family: 'Inter', system-ui, -apple-system, sans-serif;
    color: #ededeb;
    height: 44px;
  }
  .nsl-strip-inner {
    max-width: 1600px;
    margin: 0 auto;
    padding: 0 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    height: 100%;
  }
  .nsl-brand {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    text-decoration: none;
    color: #ededeb;
    font-family: 'Newsreader', 'Inter', serif;
    font-weight: 500;
    font-size: 14px;
    padding: 6px 4px;
    min-height: 32px;
    letter-spacing: 0.1px;
  }
  .nsl-brand:hover { color: #c19a5b; }
  .nsl-glyph { width: 18px; height: 18px; flex-shrink: 0; color: #c19a5b; }
  .nsl-breadcrumb {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    letter-spacing: 1.8px;
    text-transform: uppercase;
    color: #8a8a92;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .nsl-crumb-active { color: #c19a5b; }
  .nsl-sep { color: #5e5e66; }
  .nsl-link {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    text-decoration: none;
    color: #c19a5b;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    padding: 8px 12px;
    min-height: 32px;
    border-radius: 999px;
    background: rgba(193, 154, 91, 0.12);
    border: 1px solid rgba(193, 154, 91, 0.25);
    transition: background 0.2s, color 0.2s;
  }
  .nsl-link:hover {
    background: rgba(193, 154, 91, 0.22);
    color: #d4b076;
  }
  .nsl-arrow { font-family: 'Inter', sans-serif; font-size: 12px; line-height: 1; }
  /* Push the visualizer's own sticky header below the strip */
  .hero { top: 44px !important; }
  @media (max-width: 600px) {
    .nsl-strip-inner { padding: 0 14px; gap: 10px; }
    .nsl-breadcrumb .nsl-crumb,
    .nsl-breadcrumb .nsl-sep { display: none; }
    .nsl-link .nsl-link-label { display: none; }
    .nsl-brand span { font-size: 13px; }
  }
  @media (max-width: 380px) {
    .nsl-brand span { display: none; }
  }
"""

HTML_BLOCK = """
<!-- BEGIN Null Set Labs context strip -->
<div class="nsl-strip" role="navigation" aria-label="Null Set Labs">
  <div class="nsl-strip-inner">
    <a href="https://nullsetlabs.org/" class="nsl-brand" aria-label="Null Set Labs home">
      <svg class="nsl-glyph" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <defs>
          <linearGradient id="nslStripSlash" x1="20%" y1="80%" x2="80%" y2="20%">
            <stop offset="0%" stop-color="#3b82f6"/>
            <stop offset="100%" stop-color="#c19a5b"/>
          </linearGradient>
        </defs>
        <circle cx="12" cy="12" r="8" fill="none" stroke="#c19a5b" stroke-width="1.6"/>
        <line x1="5.5" y1="18.5" x2="18.5" y2="5.5" stroke="url(#nslStripSlash)" stroke-width="2.2" stroke-linecap="round"/>
      </svg>
      <span>Null Set Labs</span>
    </a>
    <div class="nsl-breadcrumb" aria-hidden="true">
      <span class="nsl-crumb">Robotics</span>
      <span class="nsl-sep">/</span>
      <span class="nsl-crumb-active">VEX Visualizer</span>
    </div>
    <a href="https://robotics.nullsetlabs.org/" class="nsl-link" aria-label="Robotics pillar">
      <span class="nsl-link-label">Robotics</span>
      <span class="nsl-arrow" aria-hidden="true">&#8599;</span>
    </a>
  </div>
</div>
<!-- END Null Set Labs context strip -->
"""


def patch(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print(f"  SKIP {path.name}: context strip already present")
        return False

    # Insert CSS right before </style>
    new_text, n_css = re.subn(
        r"(\n)?</style>",
        CSS_BLOCK + r"\g<0>",
        text,
        count=1,
    )
    if n_css == 0:
        print(f"  ERROR {path.name}: could not find </style>")
        return False

    # Insert HTML right after <body...>
    new_text, n_html = re.subn(
        r"(<body[^>]*>)",
        r"\1\n" + HTML_BLOCK.strip(),
        new_text,
        count=1,
    )
    if n_html == 0:
        print(f"  ERROR {path.name}: could not find <body>")
        return False

    path.write_text(new_text, encoding="utf-8")
    print(f"  OK {path.name}: context strip applied")
    return True


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    any_change = False
    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.is_file():
            print(f"  ERROR {arg}: not a file")
            continue
        if patch(p):
            any_change = True
    return 0 if any_change else 0


if __name__ == "__main__":
    sys.exit(main())
