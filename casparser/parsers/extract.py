"""Atom-based PDF text extraction for CAMS/KFin parsers.

Walks PDF page objects — one per text-show operation (an *atom*) — and
emits per-glyph `Char`s grouped by their parent atom. Overlay
duplicates (the KFin date-twin pattern) are filtered at the atom level,
which removes the need for the per-character overlay heuristics earlier
versions used.

Why atom-based extraction
=========================

CAS PDFs occasionally render the date column as TWO near-identical
glyph layers in the same font, offset by ~0.7pt vertically. With pure
per-glyph extraction we lose track of which glyph belongs to which
text-show op, so the two layers end up sharing one logical line and the
chars interleave by x — `2020` reads back as `22002200`, dateutil
parses it as year 2200, and downstream gains / CSV-export code consumes
garbage.

PyMuPDF and pdfminer.six handle this case naturally because their text
APIs return whole *strings* (one per text-show op), not glyphs. Each
overlay layer comes back as its own string and is easy to dedup. We can
do the same with PDFium's page-object API (``FPDFPage_GetObject`` +
``FPDFTextObj_GetText``) — each text object is one atom, the atom's
text is the un-interleaved string, and we can drop one of two atoms
that visually overlap in the same font.

Why we still need per-glyph positions
=====================================

`cams_detailed.py` uses precise per-glyph x positions for the table
column anchors. So after dedup at the atom level, we expand each
surviving atom back into per-glyph `Char`s using ``FPDFText_*``. The
`Char` / `Line` / `Page` shape stays identical to earlier versions, so
the downstream parsers don't change.

The char→atom mapping uses PDFium's own ``FPDFText_GetTextObject``,
which returns the text page object that produced each char. This is
authoritative — PDFium walks the textpage in reading order (not
content-stream order), so naive cursor-based indexing fails.

Why baseline y not bbox y
=========================

Per-char y still uses ``FPDFText_GetCharOrigin`` — the typographic
baseline, not the bbox bottom which varies with descenders. This keeps
dashes and `g`/`y`/`p` glyphs at the same y as the rest of their line.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
from typing import List, Optional

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

# Per-line baseline-clustering tolerance. With origin-based y, glyphs
# from one text-show op share an exact baseline; 1.5pt absorbs the
# small inter-atom drift you see between, e.g., a date atom and a
# description atom on the same visual row, without merging genuinely
# different rows that sit ~7pt+ apart.
Y_TOL = 1.5

# Row-clustering tolerance for the overlay dedup. Wider than `Y_TOL`
# because we want both layers of an overlay pair (typically ~0.7pt
# apart) AND the rest of the row's atoms to land in the same row. The
# actual line clustering uses the tighter `Y_TOL`.
Y_OVERLAY_ROW_TOL = 3.0

# Minimum y-offset (pts) between two same-font atoms before we treat
# them as candidates for being an overlay pair. Zero would mean "same
# physical row" — those are legitimate side-by-side cells, not overlays.
Y_OVERLAY_MIN_OFFSET = 0.05

# Minimum x-overlap (as fraction of the narrower atom's width) required
# for two same-font atoms to count as overlay duplicates rather than
# just neighbouring columns.
X_OVERLAY_MIN_FRAC = 0.5

# Buffer size for the PDFium font-name lookup.
_FONT_BUF_SIZE = 128

# Fonts we drop wholesale. Mangal is the Devanagari font NSDL/CDSL CAS
# files use to overlay Hindi translations on top of English text;
# discarding it at extraction time keeps line clustering clean.
_NON_LATIN_FONT_KEYWORDS = ("Mangal",)


# ---------------------------------------------------------------------- types


@dataclass
class Char:
    """One glyph at a known typographic position."""

    text: str
    x0: float
    y0: float  # baseline (FPDFText_GetCharOrigin)
    x1: float
    y1: float  # visual glyph top
    font: str = ""

    @property
    def h(self) -> float:
        return self.y1 - self.y0


@dataclass
class Line:
    page: int
    baseline: float
    chars: List[Char] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Reconstruct line text with spaces where x-gap is significant.

        Gap threshold = ``0.6 × median char height`` (floored at 1.5pt).
        Lower thresholds catch kerning gaps inside numerics (e.g.
        ``'12124203'`` rendered as multiple text-show ops with ~2pt
        jumps) so folio numbers and amounts don't fragment.
        """
        cs = sorted(self.chars, key=lambda c: c.x0)
        if not cs:
            return ""
        heights = sorted(c.h for c in cs)
        h_med = heights[len(heights) // 2]
        gap = max(1.5, 0.6 * h_med)
        out, prev_x1 = [], None
        for c in cs:
            if prev_x1 is not None and (c.x0 - prev_x1) > gap:
                out.append(" ")
            out.append(c.text)
            prev_x1 = c.x1
        return "".join(out)


@dataclass
class Page:
    number: int
    lines: List[Line]


@dataclass
class _Atom:
    """One PDF text-show op, with its bbox, font, and the glyphs it emitted.

    Internal to this module — `extract_pages` returns `Page`/`Line`/`Char`
    so downstream parsers don't have to know about the atom layer.
    """

    x_left: float
    x_right: float
    y_top: float
    y_bot: float
    font: str
    chars: List[Char] = field(default_factory=list)

    @property
    def width(self) -> float:
        return self.x_right - self.x_left


# ---------------------------------------------------------------------- helpers


def _is_non_latin_font(font_name: str) -> bool:
    base = font_name.split("+", 1)[-1] if "+" in font_name else font_name
    return any(kw in base for kw in _NON_LATIN_FONT_KEYWORDS)


def _strip_font_subset_prefix(name: str) -> str:
    """Strip the 6-char ``<XXXXXX>+`` PDF subset prefix so the same
    logical font compares equal across pages."""
    return name.split("+", 1)[1] if "+" in name else name


# ---------------------------------------------------------------------- API


def extract_pages(
    pdf_path: str,
    password: str,
    *,
    _doc: "Optional[pdfium.PdfDocument]" = None,
) -> List[Page]:
    """Return one `Page` per PDF page, each containing baseline-clustered
    `Line`s of `Char`s. See module docstring for the design rationale.

    ``_doc``: pre-opened document supplied by the dispatcher. When not
    provided, the function opens the PDF from `pdf_path` itself.
    """
    doc = _doc if _doc is not None else pdfium.PdfDocument(pdf_path, password=password)
    pages: List[Page] = []
    for page_num, page in enumerate(doc, start=1):
        atoms = _walk_page_atoms(page)
        atoms = _dedupe_overlay_atoms(atoms)
        pages.append(Page(number=page_num, lines=_cluster_into_lines(atoms, page_num)))
    return pages


# ---------------------------------------------------------------------- atom walk


def _walk_page_atoms(page) -> List[_Atom]:
    """Walk every text page object on `page`, capturing each atom's
    bbox, font, and the per-glyph `Char`s it contributed.

    Char-to-atom mapping uses ``FPDFText_GetTextObject(textpage, i)``,
    which is PDFium's own authoritative lookup. The textpage walks
    chars in reading order (top-down, left-to-right), so cursor-based
    indexing across page objects in stream order does not work.
    """
    page_handle = page.raw
    tp = page.get_textpage()
    tp_handle = tp.raw

    # 1. Index page objects by handle so we can look up each char's
    #    atom in O(1) below. The handle is the raw PDFium pointer
    #    returned by FPDFPage_GetObject; comparing with `ctypes`
    #    pointers requires holding the cast value (use the integer
    #    address via ctypes.addressof, or compare ctypes void_p .value).
    n_objects = pdfium_raw.FPDFPage_CountObjects(page_handle)
    font_buf = (ctypes.c_char * _FONT_BUF_SIZE)()
    left = ctypes.c_float()
    bottom = ctypes.c_float()
    right = ctypes.c_float()
    top = ctypes.c_float()

    obj_index: dict = {}  # handle-key -> _Atom (chars will be filled below)
    obj_order: List[int] = []
    for oi in range(n_objects):
        obj = pdfium_raw.FPDFPage_GetObject(page_handle, oi)
        if pdfium_raw.FPDFPageObj_GetType(obj) != pdfium_raw.FPDF_PAGEOBJ_TEXT:
            continue
        # Drop vertically-oriented text. CAMS/KFin stamp a rotated
        # watermark ("CAMSCASWS… Version:V3.4 Live-1017") down the page
        # edge; its glyphs land in the right-hand columns and bleed
        # fragments ("V", "iv", "CAMS L", "KFINTECH 4.") into the RTA /
        # scheme-name fields. The object matrix's glyph-advance vector is
        # (a, b); |b| > |a| means the run reads vertically, so it is
        # watermark noise, not content — route its chars to the drop
        # sentinel.
        _mtx = pdfium_raw.FS_MATRIX()
        if pdfium_raw.FPDFPageObj_GetMatrix(obj, ctypes.byref(_mtx)) and abs(_mtx.b) > abs(_mtx.a):
            obj_index[_obj_key(obj)] = None
            continue
        font_obj = pdfium_raw.FPDFTextObj_GetFont(obj)
        fn = pdfium_raw.FPDFFont_GetBaseFontName(font_obj, font_buf, _FONT_BUF_SIZE)
        raw_font = (
            font_buf.raw[: max(0, fn - 1)].decode("utf-8", errors="replace") if fn > 0 else ""
        )
        if _is_non_latin_font(raw_font):
            # Mark with a sentinel so chars routed here are dropped.
            obj_index[_obj_key(obj)] = None
            continue
        pdfium_raw.FPDFPageObj_GetBounds(
            obj,
            ctypes.byref(left),
            ctypes.byref(bottom),
            ctypes.byref(right),
            ctypes.byref(top),
        )
        key = _obj_key(obj)
        obj_index[key] = _Atom(
            x_left=left.value,
            x_right=right.value,
            y_top=top.value,
            y_bot=bottom.value,
            font=_strip_font_subset_prefix(raw_font),
        )
        obj_order.append(key)

    # 2. Walk per-glyph chars. For each char, ask PDFium which text
    #    object owns it and append the char to that atom's list.
    n_chars = tp.count_chars()
    ox = ctypes.c_double()
    oy = ctypes.c_double()
    for ci in range(n_chars):
        ch = tp.get_text_range(ci, 1)
        if ch in ("\r", "\n", "�"):
            continue
        x0, y0_bbox, x1, y1_bbox = tp.get_charbox(ci)
        if y1_bbox - y0_bbox <= 0 or x1 - x0 <= 0:
            continue
        text_obj = pdfium_raw.FPDFText_GetTextObject(tp_handle, ci)
        atom = obj_index.get(_obj_key(text_obj))
        if atom is None:  # None == not text, or non-Latin (dropped)
            continue
        pdfium_raw.FPDFText_GetCharOrigin(tp_handle, ci, ox, oy)
        atom.chars.append(
            Char(
                text=ch,
                x0=x0,
                y0=oy.value,
                x1=x1,
                y1=y1_bbox,
                font=atom.font,
            )
        )

    # 3. Return atoms in page-object order, dropping empties.
    return [a for k in obj_order if (a := obj_index.get(k)) is not None and a.chars]


def _obj_key(obj_ptr) -> int:
    """Return a stable hashable key for a PDFium object pointer.

    `FPDFPage_GetObject` and `FPDFText_GetTextObject` both return
    raw ctypes pointer values; the address itself is what identifies
    the underlying object.
    """
    if not obj_ptr:
        return 0
    if hasattr(obj_ptr, "value"):
        return obj_ptr.value or 0
    return ctypes.addressof(obj_ptr.contents) if obj_ptr else 0


# ---------------------------------------------------------------------- overlay dedup


def _dedupe_overlay_atoms(atoms: List[_Atom]) -> List[_Atom]:
    """Drop atoms that are overlay duplicates of another atom on the
    same visual row.

    Detection: two atoms are an overlay pair if they share a font, their
    x-ranges overlap by at least ``X_OVERLAY_MIN_FRAC`` of the narrower
    atom's width, and their ``y_top`` values differ by between
    ``Y_OVERLAY_MIN_OFFSET`` and ``Y_OVERLAY_ROW_TOL``. (Zero y-offset
    means they're legitimate side-by-side cells; large y-offset means
    they're on different rows.)

    Resolution: pick the atom whose ``y_top`` is closer to the median
    ``y_top`` of nearby atoms in the same row band. The "real" row
    atoms cluster tightly around the median; the overlay sits a hair
    above or below.
    """
    if len(atoms) < 2:
        return atoms
    # Bucket atoms into approximate rows.
    sorted_atoms = sorted(enumerate(atoms), key=lambda p: -p[1].y_top)
    rows: List[List[tuple]] = []
    anchor: Optional[float] = None
    for idx, a in sorted_atoms:
        if anchor is None or abs(a.y_top - anchor) > Y_OVERLAY_ROW_TOL:
            rows.append([(idx, a)])
            anchor = a.y_top
        else:
            rows[-1].append((idx, a))

    drop: set = set()
    for row in rows:
        if len(row) < 2:
            continue
        median_y = sorted(a.y_top for _, a in row)[len(row) // 2]
        for ii in range(len(row)):
            oi, ai = row[ii]
            if oi in drop:
                continue
            for jj in range(ii + 1, len(row)):
                oj, aj = row[jj]
                if oj in drop or not ai.font or ai.font != aj.font:
                    continue
                # x-overlap as fraction of the narrower atom's width.
                xo = min(ai.x_right, aj.x_right) - max(ai.x_left, aj.x_left)
                if xo <= 0:
                    continue
                narrower = min(ai.width, aj.width)
                if narrower <= 0 or xo / narrower < X_OVERLAY_MIN_FRAC:
                    continue
                # Same-row check: y-offset must be in the "overlay" band.
                dy = abs(ai.y_top - aj.y_top)
                if dy < Y_OVERLAY_MIN_OFFSET:
                    continue
                # Found a duplicate pair — drop the one further from median.
                drop.add(oi if abs(ai.y_top - median_y) > abs(aj.y_top - median_y) else oj)

    return [a for i, a in enumerate(atoms) if i not in drop]


# ---------------------------------------------------------------------- line clustering


def _cluster_into_lines(atoms: List[_Atom], page_num: int) -> List[Line]:
    """Cluster surviving chars into top-down `Line`s by baseline y.

    Within `Y_TOL` of the running baseline → same line; otherwise →
    new line. The running average makes the line slowly track a
    visual drift across many atoms (CAMS scheme + registrar wraps
    on different baselines are intentionally merged this way).
    """
    all_chars = [c for a in atoms for c in a.chars]
    all_chars.sort(key=lambda c: -c.y0)
    lines: List[Line] = []
    for c in all_chars:
        if lines and abs(c.y0 - lines[-1].baseline) <= Y_TOL:
            ln = lines[-1]
            ln.chars.append(c)
            n = len(ln.chars)
            ln.baseline = (ln.baseline * (n - 1) + c.y0) / n
        else:
            lines.append(Line(page=page_num, baseline=c.y0, chars=[c]))
    return lines
