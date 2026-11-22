#!/usr/bin/env python3
"""Build the web app: embed readezy_core.py into web/index.template.html and
write docs/index.html (served by GitHub Pages from the docs/ folder).

    python tools/build_web.py
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MARKER = '/*__READEZY_CORE__*/""'


def main():
    template = (ROOT / "web" / "index.template.html").read_text(encoding="utf-8")
    core = (ROOT / "readezy_core.py").read_text(encoding="utf-8")
    if template.count(MARKER) != 1:
        sys.exit(f"expected exactly one {MARKER} in the template")
    # JSON string literal, made safe to sit inside <script>…</script>
    literal = json.dumps(core).replace("</", "<\\/")
    out = template.replace(MARKER, literal)
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "index.html").write_text(out, encoding="utf-8")
    (docs / ".nojekyll").write_text("", encoding="utf-8")
    wheel = docs / "pypdf-6.21.4-py3-none-any.whl"
    if not wheel.exists():
        print("note: docs/pypdf-6.21.4-py3-none-any.whl is missing; the page will "
              "fall back to downloading it from PyPI", file=sys.stderr)
    print(f"wrote {docs / 'index.html'} ({len(out) // 1024} KB)")


if __name__ == "__main__":
    main()
