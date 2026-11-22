#!/usr/bin/env python3
"""
readezy.py - ReadEzy command line: colour a PDF's text with line gradients that
lead the eye from the end of one line to the start of the next.  The engine is
readezy_core.py (pure Python, pypdf only), shared with the web app in docs/.

Usage
-----
    pip install -r requirements.txt
    python readezy.py input.pdf output.pdf                      # pdf style, bright
    python readezy.py input.pdf output.pdf --theme blues
    python readezy.py input.pdf output.pdf --theme inverted
    python readezy.py input.pdf output.pdf --style web --theme night_blues
    python readezy.py input.pdf output.pdf --custom "#000000" "#0000FF" "#000000" "#FF0500" "#FFFFFF"
    python readezy.py input.pdf out_folder --all-themes         # one PDF per theme
    python readezy.py --list-themes
"""
import argparse
import os
import sys

import readezy_core as core


def main():
    ap = argparse.ArgumentParser(
        description="ReadEzy: colour the text of a PDF with line gradients.")
    ap.add_argument("input", nargs="?", help="input PDF")
    ap.add_argument("output", nargs="?",
                    help="output PDF (a folder when --all-themes is used)")
    ap.add_argument("--style", default=core.DEFAULT_STYLE, choices=list(core.STYLES),
                    help="pdf (default): vivid colours, pattern restarts each page; web: softer colours on off-white, pattern continues across pages")
    ap.add_argument("--theme", default=core.DEFAULT_THEME,
                    help="theme name for the chosen style (see --list-themes)")
    ap.add_argument("--custom", nargs=5, metavar=("C1", "C2", "C3", "C4", "BG"),
                    help="custom theme: four text colours and a background, e.g. "
                         + " ".join(core.CUSTOM_DEFAULT))
    ap.add_argument("--all-themes", action="store_true",
                    help="write one PDF per theme of the style into the OUTPUT folder")
    ap.add_argument("--pages", default="", help="pages to process, e.g. '1-3,7'")
    cyc = ap.add_mutually_exclusive_group()
    cyc.add_argument("--restart-each-page", dest="restart", action="store_true",
                     default=None, help="restart the colour cycle on every page "
                                        "(default for --style pdf)")
    cyc.add_argument("--continuous", dest="restart", action="store_false",
                     help="continue the colour cycle across pages (default for --style web)")
    ap.add_argument("--plain-paper", action="store_true",
                    help="web style: white paper instead of the theme's #FBFBFB")
    ap.add_argument("--list-themes", action="store_true", help="show themes and exit")
    ap.add_argument("--version", action="version", version=f"ReadEzy {core.__version__}")
    a = ap.parse_args()

    if a.list_themes:
        for style, themes in core.STYLES.items():
            print(f"--style {style}")
            for name, cols in themes.items():
                print(f"  {name:12s} {' '.join(cols)}")
        return
    if not a.input or not a.output:
        ap.error("input and output are required")
    if not a.custom and a.theme not in core.STYLES[a.style]:
        ap.error(f"theme {a.theme!r} not in style {a.style!r}: "
                 f"{', '.join(core.STYLES[a.style])}")
    try:
        for c in a.custom or []:
            core.hex_rgb(c)
    except ValueError as e:
        ap.error(str(e))

    with open(a.input, "rb") as f:
        data = f.read()
    opts = dict(style=a.style, pages=a.pages or None,
                restart_each_page=a.restart, plain_paper=a.plain_paper)

    def run(colors, out):
        try:
            result, stats = core.convert_bytes(data, colors, **opts)
        except core.ReadEzyError as e:
            sys.exit(f"error: {e}")
        with open(out, "wb") as f:
            f.write(result)
        return stats

    if a.all_themes:
        os.makedirs(a.output, exist_ok=True)
        stem = os.path.splitext(os.path.basename(a.input))[0]
        for name, cols in core.STYLES[a.style].items():
            out = os.path.join(a.output, f"{stem}_{name}.pdf")
            run(cols, out)
            print(f"{name:12s} -> {out}", file=sys.stderr)
        return
    stats = run(core.theme_colors(a.style, a.theme, a.custom), a.output)
    note = f", {stats['skipped_pages']} page(s) left unchanged" if stats["skipped_pages"] else ""
    print(f"done: {stats['colored']} characters coloured on {stats['pages']} page(s)"
          f"{note} -> {a.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
