"""
readezy_core - ReadEzy's engine: colour a PDF's text with line gradients that
lead the eye from the end of one line to the start of the next.

Pure Python (only needs pypdf), so the same code runs on the command line and
in the browser via Pyodide.

Two styles are available:

  "pdf" (default)  vivid colours on white paper; line breaks follow how the PDF
                   draws its text; the colour pattern restarts on every page.
  "web"            softer colours on off-white paper; line breaks follow the
                   visual layout; the pattern continues across pages.

Colour maths
------------
* A theme has four text colours c1..c4 and a background colour.
* Lines cycle through four gradients:
      line 0: c1 -> c2      line 1: c2 -> c1
      line 2: c3 -> c4      line 3: c4 -> c3      (then repeat)
* Every glyph gets ONE solid colour on a cosine ease,
      v = (cos(pi * (x - x_min) / (x_max - x_min) - pi) + 1) / 2,
  with RGB rounded like JavaScript's Math.round.

"pdf" style line detection
--------------------------
Glyph positions are measured on a 150 dpi canvas, in drawing order:
    x = round(device x of the text-showing operator's origin
              + advance within the operator / font scale)
where font scale = size / clamp(size, 16, 100), relative to the canvas of any
transparency group the glyph is drawn into.  A new line starts when x drops
by more than 75; positions are rebuilt for every page.

"web" style line detection
--------------------------
x is each glyph's left edge in CSS px; a new line starts when x moves left by
more than 3 px or up by more than 50 px; the cycle runs through the document.

How colours are applied
-----------------------
Every glyph's fill colour is set inside the PDF's own drawing instructions:
text-showing operators are split where the colour changes and the original fill
colour is restored afterwards.  Forms that contain text get a recoloured copy
per use.  Fonts, images, links and the text layer are untouched.  Dark themes
paint the page background first.
"""
import io
import math
import re

from pypdf import PdfReader, PdfWriter
from pypdf._cmap import charset_encoding
from pypdf._codecs import adobe_glyphs
from pypdf._codecs.core_font_metrics import CORE_FONT_METRICS
from pypdf.generic import (ArrayObject, ByteStringObject, ContentStream,
                           DecodedStreamObject, FloatObject, NameObject,
                           NumberObject, StreamObject)

__version__ = "2.1.0"

# Themes: [c1, c2, c3, c4, background]
STYLES = {
    "pdf": {
        "ocean":     ["#000000", "#1A5FD0", "#000000", "#D0461A", "#FFFFFF"],
        "forest":    ["#000000", "#127A5A", "#000000", "#8A3FB0", "#FFFFFF"],
        "ink":       ["#000000", "#283593", "#000000", "#7B1F3A", "#FFFFFF"],
        "slate":     ["#000000", "#5A6872", "#000000", "#5A6872", "#FFFFFF"],
        "midnight":  ["#F0F0F0", "#6CB4FF", "#F0F0F0", "#F2A65A", "#121212"],
        "moonlight": ["#F0F0F0", "#8C99A3", "#F0F0F0", "#8C99A3", "#121212"],
    },
    "web": {
        "ocean":     ["#000000", "#1A5FD0", "#000000", "#D0461A", "#FAF9F6"],
        "forest":    ["#000000", "#127A5A", "#000000", "#8A3FB0", "#FAF9F6"],
        "ink":       ["#000000", "#283593", "#000000", "#7B1F3A", "#FAF9F6"],
        "slate":     ["#000000", "#5A6872", "#000000", "#5A6872", "#FAF9F6"],
        "midnight":  ["#F0F0F0", "#6CB4FF", "#F0F0F0", "#F2A65A", "#121212"],
        "moonlight": ["#F0F0F0", "#8C99A3", "#F0F0F0", "#8C99A3", "#121212"],
    },
}
CUSTOM_DEFAULT = ["#000000", "#1A5FD0", "#000000", "#D0461A", "#FFFFFF"]
DEFAULT_STYLE, DEFAULT_THEME = "pdf", "ocean"

NUM_COLORS = 4
PDF_DPI = 150                 # canvas resolution used for positions
PDF_NEW_LINE_DX = 75          # recorded canvas units
FONT_SCALE_MIN, FONT_SCALE_MAX = 16, 100
WEB_NEW_LINE_DX, WEB_NEW_LINE_DY = 3, 50      # CSS px
MAX_FORM_DEPTH = 12


class ReadEzyError(Exception):
    pass


# ----------------------------------------------------------------- colours --
def hex_rgb(h):
    h = str(h).strip().lstrip("#")
    if len(h) != 6 or any(c not in "0123456789abcdefABCDEF" for c in h):
        raise ValueError(f"bad colour {h!r}, use #RRGGBB")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def is_dark(rgb):
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 120


def _is_blank(ch):
    """True for real whitespace.  Control characters (e.g. an unmapped
    ligature in a font's built-in encoding) are drawn glyphs, not blanks."""
    return ch != "" and all(c in _BLANKS for c in ch)


_BLANKS = set(" \t\n\r\u00a0\u1680\u202f\u205f\u3000") | {chr(c) for c in range(0x2000, 0x200b)}


def js_round(v):
    return math.floor(v + 0.5)    # Math.round semantics (.5 rounds up)


def gradient_color(phase, x, x_min, x_max, theme_rgb):
    """Colour of one glyph; None for a one-glyph line (keeps its original colour)."""
    c1, c2, c3, c4 = theme_rgb[:4]
    start, end = (c1, c2) if phase < 2 else (c3, c4)
    if x_max == x_min:
        return None
    k = math.pi / (x_max - x_min) * (x - x_min)
    if phase % 2 == 0:
        v = (math.cos(k - math.pi) + 1) / 2      # start -> end
    else:
        v = (math.cos(k) + 1) / 2                  # end -> start
    return tuple(js_round((e - s) * v + s) for s, e in zip(start, end))


# ---------------------------------------------------------------- matrices --
IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mmul(a, b):
    """a then b (PDF row-vector convention: p' = p * a * b)."""
    return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5])


def apply(m, x, y):
    return x * m[0] + y * m[2] + m[4], x * m[1] + y * m[3] + m[5]


def _nums(vals):
    return tuple(float(v) for v in vals)


def viewport_transform(page, scale):
    """Page viewport transform (user space -> canvas pixels)."""
    mb = _nums(page.mediabox)
    cb = _nums(page.cropbox) if "/CropBox" in page else mb
    vb = [max(min(mb[0], mb[2]), min(cb[0], cb[2])), max(min(mb[1], mb[3]), min(cb[1], cb[3])),
          min(max(mb[0], mb[2]), max(cb[0], cb[2])), min(max(mb[1], mb[3]), max(cb[1], cb[3]))]
    if vb[2] <= vb[0] or vb[3] <= vb[1]:
        vb = [min(mb[0], mb[2]), min(mb[1], mb[3]), max(mb[0], mb[2]), max(mb[1], mb[3])]
    cx, cy = (vb[2] + vb[0]) / 2, (vb[3] + vb[1]) / 2
    rot = (page.rotation or 0) % 360
    ra, rb, rc, rd = {0: (1, 0, 0, -1), 90: (0, 1, 1, 0),
                      180: (-1, 0, 0, 1), 270: (0, -1, -1, 0)}.get(rot, (1, 0, 0, -1))
    if ra == 0:
        ox, oy = abs(cy - vb[1]) * scale, abs(cx - vb[0]) * scale
        width = abs(vb[3] - vb[1]) * scale
    else:
        ox, oy = abs(cx - vb[0]) * scale, abs(cy - vb[1]) * scale
        width = abs(vb[2] - vb[0]) * scale
    return (ra * scale, rb * scale, rc * scale, rd * scale,
            ox - ra * scale * cx - rc * scale * cy,
            oy - rb * scale * cx - rd * scale * cy), width


# ------------------------------------------------------------------- fonts --
def _strip_subset(name):
    return name.split("+", 1)[1] if len(name) > 7 and name[6] == "+" else name


def _parse_cmap_unicode(data):
    """ToUnicode CMap -> {code bytes: str}."""
    out = {}
    txt = data.decode("latin1", "replace")

    def u16(h):
        try:
            return bytes.fromhex(h).decode("utf-16-be", "replace")
        except ValueError:
            return ""
    for block in re.findall(r"beginbfchar(.*?)endbfchar", txt, re.S):
        hexes = re.findall(r"<([0-9A-Fa-f\s]*)>", block)
        for src, dst in zip(hexes[0::2], hexes[1::2]):
            src = re.sub(r"\s", "", src)
            if src and len(src) % 2 == 0:
                out[bytes.fromhex(src)] = u16(re.sub(r"\s", "", dst))
    for block in re.findall(r"beginbfrange(.*?)endbfrange", txt, re.S):
        for m in re.finditer(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]*>)", block):
            lo, hi, dst = m.group(1), m.group(2), m.group(3)
            n, a, b = len(lo) // 2, int(lo, 16), int(hi, 16)
            if b - a > 65535 or n == 0:
                continue
            if dst.startswith("["):
                items = re.findall(r"<([0-9A-Fa-f]*)>", dst)
                for i, h in enumerate(items[: b - a + 1]):
                    out[(a + i).to_bytes(n, "big")] = u16(h)
            else:
                d = dst.strip("<>")
                base = int(d, 16) if d else 0
                for i in range(b - a + 1):
                    v = base + i
                    out[(a + i).to_bytes(n, "big")] = u16(f"{v:0{len(d)}X}") if d else ""
    return out


def _codespace_ranges(data):
    txt = data.decode("latin1", "replace")
    ranges = []
    for block in re.findall(r"begincodespacerange(.*?)endcodespacerange", txt, re.S):
        hexes = re.findall(r"<([0-9A-Fa-f]+)>", block)
        for lo, hi in zip(hexes[0::2], hexes[1::2]):
            ranges.append((len(lo) // 2, bytes.fromhex(lo), bytes.fromhex(hi)))
    return sorted(ranges)


def _truetype_missing_glyphs(data):
    """Glyph ids that are empty in a TrueType font program: glyphs whose
    'glyf' data is 12 bytes or less, e.g. spaces.
    Returns (missing set, numGlyphs) or None if the font can't be parsed."""
    import struct
    try:
        num_tables = struct.unpack(">H", data[4:6])[0]
        tables = {}
        for i in range(num_tables):
            rec = data[12 + 16 * i: 28 + 16 * i]
            tag, _, off, length = struct.unpack(">4sIII", rec)
            tables[tag] = (off, length)
        head_off = tables[b"head"][0]
        long_loca = struct.unpack(">h", data[head_off + 50: head_off + 52])[0] == 1
        num_glyphs = struct.unpack(">H", data[tables[b"maxp"][0] + 4: tables[b"maxp"][0] + 6])[0]
        loff, llen = tables[b"loca"]
        if long_loca:
            n = min(num_glyphs + 1, llen // 4)
            offs = list(struct.unpack(f">{n}I", data[loff: loff + 4 * n]))
        else:
            n = min(num_glyphs + 1, llen // 2)
            offs = [2 * v for v in struct.unpack(f">{n}H", data[loff: loff + 2 * n])]
        if len(offs) < num_glyphs + 1:
            return None
        order = sorted(range(num_glyphs + 1), key=lambda i: (offs[i], i))
        end = {}
        for a, b in zip(order, order[1:]):
            end[a] = offs[b]
        missing = {i for i in range(num_glyphs) if end.get(i, offs[i]) - offs[i] <= 12}
        return missing, num_glyphs
    except (KeyError, struct.error, IndexError, ValueError):
        return None


class FontInfo:
    """What the interpreter needs from a font: how to split shown strings into
    codes, each code's advance width, whether it is a word space, and its
    unicode text (to recognise whitespace)."""

    def __init__(self, fdict):
        self.ok, self.type3, self.vertical = True, False, False
        self.split = self._split1
        self.widths, self.default_w, self.scale = {}, 0.0, 0.001
        self.to_unicode = {}
        self.table = None
        self.tt_missing = None          # (missing gids, numGlyphs, cid->gid) for CID TrueType
        self.tt_simple = False          # embedded simple TrueType
        subtype = str(fdict.get("/Subtype", ""))
        tu = fdict.get("/ToUnicode")
        if tu is not None:
            try:
                self.to_unicode = _parse_cmap_unicode(tu.get_object().get_data())
            except Exception:
                self.to_unicode = {}
        if subtype == "/Type0":
            self._init_type0(fdict)
        else:
            if subtype == "/Type3":
                self.type3 = True
                fm = fdict.get("/FontMatrix")
                self.scale = float(fm[0]) if fm else 0.001
            self._init_simple(fdict)

    # --- simple fonts (Type1, TrueType, MMType1, Type3) ---
    def _init_simple(self, fdict):
        base = _strip_subset(str(fdict.get("/BaseFont", "")).lstrip("/"))
        enc = fdict.get("/Encoding")
        enc = enc.get_object() if enc is not None else None
        table = None
        if isinstance(enc, NameObject) and str(enc) in charset_encoding:
            table = list(charset_encoding[str(enc)])
        elif enc is not None and hasattr(enc, "get"):
            be = enc.get("/BaseEncoding")
            table = list(charset_encoding.get(str(be), charset_encoding["/StandardEncoding"]))
            diffs, code = enc.get("/Differences"), 0
            if diffs is not None:
                for item in diffs:
                    if isinstance(item, (int, NumberObject)):
                        code = int(item)
                    else:
                        if 0 <= code < 256:
                            table[code] = adobe_glyphs.get(str(item), table[code])
                        code += 1
        if table is None:
            if base.startswith("Symbol"):
                table = list(charset_encoding["/Symbol"])
            elif base.startswith("ZapfDingbats"):
                table = list(charset_encoding["/ZapfDingbats"])
            else:
                table = list(charset_encoding["/StandardEncoding"])
        self.table = table
        fdesc = fdict.get("/FontDescriptor")
        fdesc = fdesc.get_object() if fdesc is not None else None
        self.tt_simple = fdesc is not None and "/FontFile2" in fdesc
        widths = fdict.get("/Widths")
        if widths is not None:
            first = int(fdict.get("/FirstChar", 0))
            for i, w in enumerate(widths.get_object()):
                try:
                    self.widths[first + i] = float(w)
                except (TypeError, ValueError):
                    pass
            fd = fdict.get("/FontDescriptor")
            fd = fd.get_object() if fd is not None else None
            if fd is not None and "/MissingWidth" in fd:
                self.default_w = float(fd["/MissingWidth"])
        else:                                         # standard 14 font
            metrics = CORE_FONT_METRICS.get(base) or CORE_FONT_METRICS.get(
                {"Arial": "Helvetica"}.get(base, base.replace("-", ",")), None)
            if metrics is None:
                for k in CORE_FONT_METRICS:
                    if base.startswith(k):
                        metrics = CORE_FONT_METRICS[k]
            if metrics is not None:
                cw = metrics.character_widths
                self.default_w = float(cw.get("default", 0))
                for code, ch in enumerate(table):
                    if ch and ch in cw:
                        self.widths[code] = float(cw[ch])

    @staticmethod
    def _split1(b):
        return [b[i:i + 1] for i in range(len(b))]

    # --- composite fonts ---
    def _init_type0(self, fdict):
        enc = fdict.get("/Encoding")
        enc = enc.get_object() if enc is not None else None
        if isinstance(enc, NameObject):
            name = str(enc)
            self.vertical = name.endswith("-V")
            if name in ("/Identity-H", "/Identity-V") or "UCS2" in name or "UTF16" in name:
                self.split = lambda b: [b[i:i + 2] for i in range(0, len(b), 2)]
            else:
                self.ok = False
        elif isinstance(enc, StreamObject):
            if int(enc.get("/WMode", 0)) == 1:
                self.vertical = True
            ranges = _codespace_ranges(enc.get_data())
            if not ranges:
                self.ok = False
            else:
                def split(b, ranges=ranges):
                    out, i = [], 0
                    while i < len(b):
                        for n, lo, hi in ranges:
                            c = b[i:i + n]
                            if len(c) == n and all(l <= x <= h for x, l, h in zip(c, lo, hi)):
                                break
                        else:
                            n = ranges[0][0]
                        out.append(b[i:i + n])
                        i += n
                    return out
                self.split = split
        else:
            self.ok = False
        desc = fdict.get("/DescendantFonts")
        desc = desc.get_object()[0].get_object() if desc is not None else {}
        self.default_w = float(desc.get("/DW", 1000))
        fdesc = desc.get("/FontDescriptor")
        fdesc = fdesc.get_object() if fdesc is not None else None
        if (str(desc.get("/Subtype", "")) == "/CIDFontType2" and fdesc is not None
                and "/FontFile2" in fdesc):
            try:
                info = _truetype_missing_glyphs(fdesc["/FontFile2"].get_object().get_data())
            except Exception:
                info = None
            if info is not None:
                c2g = desc.get("/CIDToGIDMap")
                c2g = c2g.get_object() if c2g is not None else None
                gid_map = None
                if isinstance(c2g, StreamObject):
                    raw = c2g.get_data()
                    gid_map = [int.from_bytes(raw[i:i + 2], "big") for i in range(0, len(raw) - 1, 2)]
                self.tt_missing = (info[0], info[1], gid_map)
        w = desc.get("/W")
        if w is not None:
            w = [x.get_object() if hasattr(x, "get_object") else x for x in w.get_object()]
            i = 0
            while i < len(w):
                try:
                    first = int(w[i])
                    if i + 1 < len(w) and isinstance(w[i + 1], list):
                        for j, val in enumerate(w[i + 1]):
                            self.widths[first + j] = float(val)
                        i += 2
                    else:
                        last, val = int(w[i + 1]), float(w[i + 2])
                        if last - first <= 65535:
                            for c in range(first, last + 1):
                                self.widths[c] = val
                        i += 3
                except (TypeError, ValueError, IndexError):
                    break

    def width(self, code):
        """Advance in text space per unit font size."""
        return self.widths.get(int.from_bytes(code, "big"), self.default_w) * self.scale

    def text(self, code):
        if code in self.to_unicode:
            return self.to_unicode[code]
        if self.table is not None and len(code) == 1:
            ch = self.table[code[0]]
            return ch if ch else ""
        return ""

    def in_font(self, code):
        """False for glyphs that draw nothing (empty or absent TrueType
        glyphs); they don't take part in line detection."""
        if self.tt_missing is not None:
            missing, num, gid_map = self.tt_missing
            cid = int.from_bytes(code, "big")        # Identity-style CMaps: code == CID
            gid = cid if gid_map is None else (gid_map[cid] if cid < len(gid_map) else -1)
            return 0 <= gid < num and gid not in missing
        if self.tt_simple:
            t = self.text(code)
            return not (t and not t.strip())
        return True

    @staticmethod
    def is_word_space(code):
        return code == b" "


# ------------------------------------------------------------- interpreter --
_FILL_OPS = {b"g", b"rg", b"k", b"cs", b"sc", b"scn"}
_SHOW_OPS = {b"Tj", b"TJ", b"'", b'"'}


class Glyph:
    __slots__ = ("ch", "rx", "ry", "wx", "wy")

    def __init__(self, ch, rx, ry, wx, wy):
        self.ch, self.rx, self.ry, self.wx, self.wy = ch, rx, ry, wx, wy


class Show:
    """Placeholder for a rewritten text-showing operator."""
    __slots__ = ("segments", "restore")

    def __init__(self, segments, restore):
        self.segments, self.restore = segments, restore


class FormUse:
    """Placeholder for a recoloured copy of a form XObject."""
    __slots__ = ("original", "ops", "xobjects", "name")

    def __init__(self, original, ops, xobjects, name):
        self.original, self.ops, self.xobjects, self.name = original, ops, xobjects, name


class _State:
    __slots__ = ("ctm", "fill", "font", "fs", "tc", "tw", "th", "tl", "rise")

    def __init__(self):
        self.ctm, self.fill, self.font, self.fs = IDENT, (None, None), None, 0.0
        self.tc = self.tw = self.tl = self.rise = 0.0
        self.th = 1.0

    def copy(self):
        s = _State.__new__(_State)
        for k in _State.__slots__:
            setattr(s, k, getattr(self, k))
        return s


def _resources_of(obj):
    node = obj
    for _ in range(32):
        if node is None:
            return None
        r = node.get("/Resources")
        if r is not None:
            return r.get_object()
        p = node.get("/Parent")
        node = p.get_object() if p is not None else None
    return None


class PageInterpreter:
    def __init__(self, writer, page, form_counter):
        self.writer, self.page = writer, page
        self.vt, self.canvas_w = viewport_transform(page, PDF_DPI / 72)
        self.vt_css, _ = viewport_transform(page, 96 / 72)
        self.glyphs = []
        self.font_cache = {}
        self.form_counter = form_counter
        self.n_forms = 0

    def _font(self, fobj):
        key = id(fobj)
        if key not in self.font_cache:
            try:
                self.font_cache[key] = FontInfo(fobj)
            except Exception:
                fi = FontInfo.__new__(FontInfo)
                fi.ok = False
                self.font_cache[key] = fi
        return self.font_cache[key]

    @staticmethod
    def _restore(fill):
        if fill == (None, None):
            return [([NumberObject(0)], b"g")]
        return [op for op in fill if op is not None]

    def run(self):
        content = self.page.get_contents()
        if content is None:
            return None
        ops = content.operations
        return self.walk(ops, _resources_of(self.page), _State(), (0, 0), 0)

    def walk(self, ops, resources, st, group_off, depth):
        fonts = resources.get("/Font") if resources is not None else None
        fonts = fonts.get_object() if fonts is not None else None
        xobjs = resources.get("/XObject") if resources is not None else None
        xobjs = xobjs.get_object() if xobjs is not None else None
        out, stack = [], []
        tm = tlm = IDENT
        for operands, op in ops:
            if op == b"INLINE IMAGE":
                out.append((operands, op))
                continue
            if op == b"q":
                stack.append(st.copy())
            elif op == b"Q":
                if stack:
                    st = stack.pop()
            elif op == b"cm" and len(operands) == 6:
                st.ctm = mmul(_nums(operands), st.ctm)
            elif op in _FILL_OPS:
                if op in (b"g", b"rg", b"k"):
                    st.fill = (None, (operands, op))
                elif op == b"cs":
                    st.fill = ((operands, op), None)
                else:
                    st.fill = (st.fill[0], (operands, op))
            elif op == b"BT":
                tm = tlm = IDENT
            elif op == b"Tf" and len(operands) == 2:
                f = fonts.get(operands[0]) if fonts is not None else None
                st.font = self._font(f.get_object()) if f is not None else None
                st.fs = float(operands[1])
            elif op == b"Tc" and operands:
                st.tc = float(operands[0])
            elif op == b"Tw" and operands:
                st.tw = float(operands[0])
            elif op == b"Tz" and operands:
                st.th = float(operands[0]) / 100
            elif op == b"TL" and operands:
                st.tl = float(operands[0])
            elif op == b"Ts" and operands:
                st.rise = float(operands[0])
            elif op in (b"Td", b"TD") and len(operands) == 2:
                tx, ty = float(operands[0]), float(operands[1])
                if op == b"TD":
                    st.tl = -ty
                tlm = mmul((1, 0, 0, 1, tx, ty), tlm)
                tm = tlm
            elif op == b"Tm" and len(operands) == 6:
                tm = tlm = _nums(operands)
            elif op == b"T*":
                tlm = mmul((1, 0, 0, 1, 0, -st.tl), tlm)
                tm = tlm
            elif op in _SHOW_OPS:
                if op == b"'" or op == b'"':
                    if op == b'"' and len(operands) == 3:
                        st.tw, st.tc = float(operands[0]), float(operands[1])
                        out.append(([operands[0]], b"Tw"))
                        out.append(([operands[1]], b"Tc"))
                    out.append(([], b"T*"))
                    tlm = mmul((1, 0, 0, 1, 0, -st.tl), tlm)
                    tm = tlm
                if not operands:
                    out.append((operands, op))
                    continue
                shown = operands[-1]
                elements = list(shown) if op == b"TJ" else [shown]
                show, tm = self._show(elements, st, tm, group_off)
                if show is None:            # font we can't interpret: keep as is
                    out.append((operands, op) if op in (b"Tj", b"TJ") else ([shown], b"Tj"))
                else:
                    out.append(show)
                continue
            elif op == b"Do" and operands and xobjs is not None and depth < MAX_FORM_DEPTH:
                use = self._form(operands[0], xobjs, resources, st, group_off, depth)
                if use is not None:
                    out.append(use)
                    continue
            out.append((operands, op))
        return out

    def _show(self, elements, st, tm, group_off):
        font = st.font
        if font is None or not font.ok or font.vertical:
            return None, tm
        fs, th = st.fs, st.th
        size = abs(fs) or 1.0
        o = size / min(max(size, FONT_SCALE_MIN), FONT_SCALE_MAX)   # font scale
        trm0 = mmul(tm, st.ctm)
        gx, gy = apply(self.vt, *apply(trm0, 0, st.rise))           # run origin
        x_acc = 0.0
        segments = []
        for el in elements:
            if isinstance(el, (bytes, str)) or hasattr(el, "original_bytes"):
                raw = el.original_bytes if hasattr(el, "original_bytes") else (
                    el if isinstance(el, bytes) else el.encode("latin1", "replace"))
                for code in font.split(raw):
                    adv = font.width(code) * fs + st.tc + (st.tw if font.is_word_space(code) else 0.0)
                    if font.type3 or not font.in_font(code):
                        segments.append(("code", code, None))     # draws nothing
                    else:
                        ux, uy = apply(mmul(tm, st.ctm), 0, st.rise)
                        wx, wy = apply(self.vt_css, ux, uy)
                        rx = js_round(gx + x_acc / o) - group_off[0]
                        ry = js_round(gy) - group_off[1]
                        self.glyphs.append(Glyph(font.text(code), rx, ry, wx, wy))
                        segments.append(("code", code, len(self.glyphs) - 1))
                    x_acc += adv
                    tm = mmul((1, 0, 0, 1, adv * th, 0), tm)
            else:
                try:
                    num = float(el)
                except (TypeError, ValueError):
                    continue
                d = -num / 1000 * fs
                x_acc += d
                tm = mmul((1, 0, 0, 1, d * th, 0), tm)
                segments.append(("num", el, None))
        return Show(segments, self._restore(st.fill)), tm

    def _form(self, name, xobjs, parent_res, st, group_off, depth):
        xo = xobjs.get(name)
        if xo is None:
            return None
        xo = xo.get_object()
        if str(xo.get("/Subtype", "")) != "/Form":
            return None
        m = _nums(xo["/Matrix"]) if "/Matrix" in xo else IDENT
        inner = st.copy()
        inner.ctm = mmul(m, st.ctm)
        off = group_off
        if "/Group" in xo and "/BBox" in xo:          # transparency group canvas
            x0, y0, x1, y1 = _nums(xo["/BBox"])
            full = mmul(inner.ctm, self.vt)
            pts = [apply(full, x, y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
            xs = [p[0] - group_off[0] for p in pts]
            ys = [p[1] - group_off[1] for p in pts]
            lx, hx, ly, hy = max(min(xs), 0.0), max(xs), max(min(ys), 0.0), max(ys)
            if hx > lx and hy > ly:                    # Util.intersect with the canvas
                off = (group_off[0] + math.floor(lx), group_off[1] + math.floor(ly))
        try:
            ops = ContentStream(xo, self.writer).operations
        except Exception:
            return None
        n_before = len(self.glyphs)
        res = _resources_of(xo) or parent_res
        new_ops = self.walk(ops, res, inner, off, depth + 1)
        if len(self.glyphs) == n_before and not any(isinstance(o, (Show, FormUse)) for o in new_ops):
            return None                                # no text inside: keep original
        self.form_counter[0] += 1
        return FormUse(xo, new_ops, xobjs, NameObject(f"/RzForm{self.form_counter[0]}"))


# ------------------------------------------------------------- colouring --
def pdf_style_colors(glyphs, theme_rgb):
    """\"pdf\" style colour per glyph (None = keep the original)."""
    lines, line_map = [], {}
    prev, idx, color = None, -1, -1
    for g in glyphs:                                   # split into lines
        if prev is None or g.rx < prev - PDF_NEW_LINE_DX:
            if idx >= 0:
                lines[idx][1] = prev
            idx += 1
            color = (color + 1) % NUM_COLORS
            lines.append([g.rx, None, color])
        line_map[(g.ch, g.rx, g.ry)] = idx
        prev = g.rx
    if idx >= 0:
        lines[idx][1] = prev
    out = []
    for g in glyphs:                                   # colour along each line
        if _is_blank(g.ch):
            out.append(None)                           # whitespace
            continue
        xmin, xmax, phase = lines[line_map[(g.ch, g.rx, g.ry)]]
        out.append(gradient_color(phase, g.rx, xmin, xmax, theme_rgb))
    return out


class WebColorer:
    """\"web\" style line detection; the cycle continues across calls."""

    def __init__(self, theme_rgb):
        self.theme, self.phase = theme_rgb, 0

    def colors(self, glyphs):
        out = [None] * len(glyphs)
        line, xfirst, xlast, ylast = [], None, None, None

        def flush():
            if not line:
                return
            x_last = glyphs[line[-1]].wx
            for i in line:
                out[i] = gradient_color(self.phase, glyphs[i].wx, xfirst, x_last, self.theme)
            self.phase = (self.phase + 1) % NUM_COLORS
            line.clear()

        for i, g in enumerate(glyphs):
            if _is_blank(g.ch):
                continue                               # spaces don't take part
            if xlast is None:
                xfirst = xlast = g.wx
                ylast = g.wy
            if g.wx - xlast < -WEB_NEW_LINE_DX or g.wy - ylast < -WEB_NEW_LINE_DY:
                flush()
                xfirst = g.wx
            xlast, ylast = g.wx, g.wy
            line.append(i)
        flush()
        return out


# --------------------------------------------------------- writing output --
def _rg(col):
    return ([FloatObject(round(c / 255, 4)) for c in col], b"rg")


def _materialise(items, colors, writer):
    ops = []
    for it in items:
        if isinstance(it, Show):
            arr, active = [], object()

            def flush():
                if arr:
                    ops.append(([ArrayObject(arr)], b"TJ"))
                arr.clear()
            for kind, val, gi in it.segments:
                if kind == "num":
                    arr.append(val)
                    continue
                col = colors[gi] if gi is not None else None
                if col != active:
                    flush()
                    ops.extend(it.restore if col is None else [_rg(col)])
                    active = col
                if arr and isinstance(arr[-1], ByteStringObject):
                    arr[-1] = ByteStringObject(bytes(arr[-1]) + val)
                else:
                    arr.append(ByteStringObject(val))
            flush()
            ops.extend(it.restore)
        elif isinstance(it, FormUse):
            stream = DecodedStreamObject()
            for k, v in it.original.items():
                if k not in ("/Length", "/Filter", "/DecodeParms"):
                    stream[NameObject(k)] = v
            cs = ContentStream(None, writer)
            cs.operations = _materialise(it.ops, colors, writer)
            stream.set_data(cs.get_data())
            it.xobjects[it.name] = writer._add_object(stream)
            ops.append(([it.name], b"Do"))
        else:
            ops.append(it)
    return ops


def parse_pages(spec, n):
    if not spec:
        return list(range(n))
    out = []
    for part in str(spec).split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            a = int(a) if a.strip() else 1
            b = int(b) if b.strip() else n
            out.extend(range(a - 1, min(b, n)))
        elif part:
            out.append(int(part) - 1)
    return sorted({p for p in out if 0 <= p < n})


def convert_bytes(data, theme_colors, style=DEFAULT_STYLE, pages=None,
                  restart_each_page=None, plain_paper=False, progress=None):
    """Colour a PDF given as bytes; returns (output bytes, stats dict).

    progress(done, total) is called after every page."""
    theme_rgb = [hex_rgb(c) for c in theme_colors]
    if len(theme_rgb) != 5:
        raise ValueError("a theme needs five colours: c1 c2 c3 c4 background")
    if style not in STYLES:
        raise ValueError(f"unknown style {style!r}")
    night = is_dark(theme_rgb[4])
    paper = (255, 255, 255) if plain_paper and not night else theme_rgb[4]
    background = None if paper == (255, 255, 255) else paper
    if restart_each_page is None:
        restart_each_page = (style == "pdf")
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as e:
        raise ReadEzyError(f"This file could not be read as a PDF ({e}).") from e
    if reader.is_encrypted:
        try:
            ok = reader.decrypt("")
        except Exception:
            ok = 0
        if not ok:
            raise ReadEzyError("This PDF is password protected. Unlock it first.")
    writer = PdfWriter(clone_from=reader)
    page_list = parse_pages(pages, len(writer.pages))
    web = WebColorer(theme_rgb)
    form_counter = [0]
    stats = {"pages": len(page_list), "glyphs": 0, "colored": 0, "skipped_pages": 0}
    for n, pno in enumerate(page_list):
        page = writer.pages[pno]
        try:
            interp = PageInterpreter(writer, page, form_counter)
            items = interp.run()
        except Exception:
            items = None
        if items is None:
            stats["skipped_pages"] += 1
        else:
            if style == "pdf":
                colors = pdf_style_colors(interp.glyphs, theme_rgb)
            else:
                if restart_each_page:
                    web.phase = 0
                colors = web.colors(interp.glyphs)
            ops = _materialise(items, colors, writer)
            if background is not None:
                r, g, b = (FloatObject(round(c / 255, 4)) for c in background)
                ops = [([], b"q"), ([r, g, b], b"rg"),
                       ([NumberObject(-10000), NumberObject(-10000),
                         NumberObject(30000), NumberObject(30000)], b"re"),
                       ([], b"f"), ([], b"Q")] + ops
            cs = ContentStream(None, writer)
            cs.operations = ops
            page.replace_contents(cs)
            stats["glyphs"] += len(interp.glyphs)
            stats["colored"] += sum(c is not None for c in colors)
        if progress is not None:
            progress(n + 1, len(page_list))
    writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
    for page in writer.pages:
        try:
            page.compress_content_streams()
        except Exception:
            pass
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue(), stats


def theme_colors(style=DEFAULT_STYLE, theme=DEFAULT_THEME, custom=None):
    if custom:
        return list(custom)
    themes = STYLES[style]
    if theme not in themes:
        raise ValueError(f"theme {theme!r} not in style {style!r}: {', '.join(themes)}")
    return themes[theme]
