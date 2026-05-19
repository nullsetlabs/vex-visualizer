"""
Apply the Null Set Labs palette harmonization to a VEX Visualizer HTML file.

This script swaps the visualizer's indigo/violet primary accent for the Null
Set Labs brass tone (#c19a5b), drops the purple cast from the hero backdrop
and title shimmer, and harmonizes a handful of inline link and shadow colors.

Data-semantic colors are preserved: tier badges (Elite/Contender/Competitive/
Developing/Rising), the gold spotlight on team 6121E, and chart bar palettes
inside team detail views are left untouched.

Idempotent: if the marker is already present, the script reports and exits
without changing the file.

Usage:
    python apply_palette_harmonization.py <path_to_html_file>

Example:
    python apply_palette_harmonization.py index.html
    python apply_palette_harmonization.py vex_visualizer_template.html

Recommended order:
    python apply_context_strip.py index.html
    python apply_palette_harmonization.py index.html
"""

import sys
import re
from pathlib import Path

MARKER = "/* NSL_PALETTE_HARMONIZATION */"

# Each entry: (description, find_pattern, replace_value, expected_count)
# Patterns are plain strings used with str.replace; counts are enforced.
REPLACEMENTS = [
    # 1. Root token: primary accent → brass
    (
        "root --accent token",
        "--accent: #6366f1;",
        "--accent: #c19a5b;",
        1,
    ),
    # 2. Root token: secondary accent → lighter brass
    (
        "root --accent2 token",
        "--accent2: #818cf8;",
        "--accent2: #d4b076;",
        1,
    ),
    # 3. Hero logo block gradient (purple → brass)
    (
        "hero-logo gradient",
        "background: linear-gradient(135deg, #6366f1, #8b5cf6, #ec4899);",
        "background: linear-gradient(135deg, #c19a5b, #d4b076, #c19a5b);",
        1,
    ),
    # 4. Hero logo shadow (indigo glow → brass glow)
    (
        "hero-logo shadow",
        "box-shadow: 0 4px 16px rgba(99,102,241,0.3);",
        "box-shadow: 0 4px 16px rgba(193,154,91,0.3);",
        1,
    ),
    # 5. Hero backdrop gradient (drop purple start point)
    (
        "hero backdrop gradient",
        "background: linear-gradient(135deg, #1a1040 0%, #0f172a 50%, #0c1a2e 100%);",
        "background: linear-gradient(135deg, #0f172a 0%, #0a0a0f 50%, #0c1a2e 100%);",
        1,
    ),
    # 6. Search input gradient backdrop (indigo/violet → brass tint)
    (
        "search-input gradient",
        "background: linear-gradient(135deg, rgba(99,102,241,0.08), rgba(139,92,246,0.08));",
        "background: linear-gradient(135deg, rgba(193,154,91,0.08), rgba(212,176,118,0.06));",
        1,
    ),
    # 7. Search input idle shadow
    (
        "search-input shadow",
        "box-shadow: 0 0 16px rgba(99,102,241,0.15), inset 0 1px 2px rgba(0,0,0,0.2);",
        "box-shadow: 0 0 16px rgba(193,154,91,0.18), inset 0 1px 2px rgba(0,0,0,0.2);",
        1,
    ),
    # 8 & 9. Division card / team card hover shadow (same string appears twice)
    (
        "card hover shadows",
        "box-shadow: 0 8px 24px rgba(99,102,241,0.15);",
        "box-shadow: 0 8px 24px rgba(193,154,91,0.18);",
        2,
    ),
    # 10. Stat tile highlight background
    (
        "stat-tile highlight bg",
        "background: rgba(99,102,241,0.08);",
        "background: rgba(193,154,91,0.10);",
        1,
    ),
    # 11. Override / season card subtle bg
    (
        "override card bg",
        "background: rgba(99,102,241,0.06); border: 1px solid var(--border); border-radius: 8px;",
        "background: rgba(193,154,91,0.08); border: 1px solid var(--border); border-radius: 8px;",
        1,
    ),
    # 12. Override active state gradient + border + shadow trio (block replace)
    (
        "override active gradient",
        "background: linear-gradient(135deg, rgba(99,102,241,0.25), rgba(139,92,246,0.2));",
        "background: linear-gradient(135deg, rgba(193,154,91,0.22), rgba(212,176,118,0.16));",
        1,
    ),
    (
        "override active border",
        "border: 2px solid rgba(129,140,248,0.65);",
        "border: 2px solid rgba(193,154,91,0.55);",
        1,
    ),
    (
        "override active shadow",
        "box-shadow: 0 0 0 1px rgba(99,102,241,0.15), 0 2px 16px rgba(99,102,241,0.35);",
        "box-shadow: 0 0 0 1px rgba(193,154,91,0.18), 0 2px 16px rgba(193,154,91,0.32);",
        1,
    ),
    # 13. Bottom nav active state
    (
        "bottom-nav active bg",
        "background: rgba(99,102,241,0.1);",
        "background: rgba(193,154,91,0.12);",
        1,
    ),
]

# Shimmer gradient (appears 3x verbatim across hero h1, hero-tagline em, and
# spotlight title). Swap purple/pink stops for brass + cream + white sweep.
SHIMMER_OLD = (
    "      #818cf8 0%, #c084fc 15%, #f0abfc 30%,\n"
    "      #fde68a 45%, #fff 50%,\n"
    "      #fde68a 55%, #f0abfc 70%, #c084fc 85%, #818cf8 100%"
)
SHIMMER_NEW = (
    "      #c19a5b 0%, #d4b076 15%, #ede4cf 30%,\n"
    "      #f5e6c8 45%, #ffffff 50%,\n"
    "      #f5e6c8 55%, #ede4cf 70%, #d4b076 85%, #c19a5b 100%"
)
SHIMMER_EXPECTED = 3

# Inline SVG fills on the robot icon (3 circles + 2 rects in the head).
SVG_OLD = 'fill="#6366f1"'
SVG_NEW = 'fill="#c19a5b"'
SVG_EXPECTED = 5

# Inline link colors in footer/credit text + spotlight team links.
LINK_HEX_OLD = "color:#6366f1"
LINK_HEX_NEW = "color:#c19a5b"

LINK_BG_OLD = "background:rgba(99,102,241,0.08)"
LINK_BG_NEW = "background:rgba(193,154,91,0.10)"


def patch(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print(f"  SKIP {path.name}: palette harmonization already present")
        return False

    changes = []
    failed = []

    # Apply each scoped replacement, enforcing the expected count.
    new_text = text
    for label, find, repl, expected in REPLACEMENTS:
        count = new_text.count(find)
        if count != expected:
            failed.append(f"{label}: found {count}, expected {expected}")
            continue
        new_text = new_text.replace(find, repl, expected)
        changes.append(f"{label} (x{expected})")

    # Shimmer gradient (3 occurrences)
    sh_count = new_text.count(SHIMMER_OLD)
    if sh_count != SHIMMER_EXPECTED:
        failed.append(
            f"shimmer gradient: found {sh_count}, expected {SHIMMER_EXPECTED}"
        )
    else:
        new_text = new_text.replace(SHIMMER_OLD, SHIMMER_NEW, SHIMMER_EXPECTED)
        changes.append(f"shimmer gradient (x{SHIMMER_EXPECTED})")

    # SVG robot fills
    svg_count = new_text.count(SVG_OLD)
    if svg_count != SVG_EXPECTED:
        failed.append(
            f"robot svg fills: found {svg_count}, expected {SVG_EXPECTED}"
        )
    else:
        new_text = new_text.replace(SVG_OLD, SVG_NEW, SVG_EXPECTED)
        changes.append(f"robot svg fills (x{SVG_EXPECTED})")

    # Inline link colors (replace all)
    link_hex_count = new_text.count(LINK_HEX_OLD)
    if link_hex_count > 0:
        new_text = new_text.replace(LINK_HEX_OLD, LINK_HEX_NEW)
        changes.append(f"inline link color (x{link_hex_count})")

    link_bg_count = new_text.count(LINK_BG_OLD)
    if link_bg_count > 0:
        new_text = new_text.replace(LINK_BG_OLD, LINK_BG_NEW)
        changes.append(f"inline link bg (x{link_bg_count})")

    if failed:
        print(f"  ERROR {path.name}: aborting, the following did not match:")
        for f in failed:
            print(f"    - {f}")
        return False

    # Insert the marker as the first line inside :root so it travels with
    # the changes and the idempotency check finds it.
    new_text, marker_count = re.subn(
        r"(:root \{\n)",
        r"\g<1>    " + MARKER + "\n",
        new_text,
        count=1,
    )
    if marker_count == 0:
        print(f"  ERROR {path.name}: could not insert marker into :root")
        return False

    path.write_text(new_text, encoding="utf-8")
    print(f"  OK {path.name}: palette harmonization applied")
    for c in changes:
        print(f"     - {c}")
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
