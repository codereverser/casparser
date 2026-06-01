"""Page-object based block extractor for NSDL/CDSL.

PROD's PyMuPDF flow:
  extractDICT(sort=True) → blocks → lines → spans
  - one *span* ≈ one PDF text-show operation
  - lines within a block joined by `\\n`, blocks joined by `\\u2029`
  - PROD's `extract_blocks` further merges close-y lines into one
    `\\t\\t`-joined "group line" within the block.

pypdfium2's page-object API exposes the same granularity directly:
`FPDFPage_GetObject` yields one object per text-show op, with bounds
from `FPDFPageObj_GetBounds` and text from `FPDFTextObj_GetText`.

This is strictly better than the content-stream-char walker in nsdl.py
because each text-show op stays an atomic unit — no need to guess its
boundary from x-jumps, so cells whose internal gap is smaller than
inter-column gap (the NSDL/CDSL MF Holdings case) are no longer
mis-merged.

Layout reconstruction:
1.  Extract one atom per text-show op (drop Mangal/Devanagari font).
2.  Dedup atoms by `(x_left, y_top, text)` — CAS PDFs render the
    top banner twice, both copies are present as separate objects.
3.  Cluster atoms into *raw lines* by y_top within `Y_LINE_TOL`.
4.  Cluster raw lines into *logical blocks* — consecutive raw lines
    whose top-to-top y-gap is `≤ Y_BLOCK_TOL`. This is the analogue
    of PyMuPDF's block grouping plus PROD's close-y line merging.
5.  Within a block, group atoms into *columns* by x-range overlap,
    anchored on the topmost atom in each column. Within a column,
    sort atoms top-down and join with `\\n`. Sort columns left→right
    and join with `\\t\\t`.
6.  Join blocks (within page) with `\\u2029`, pages with `\\u2029` too.

The output is compatible with `text.split("\\u2029")` in
`process_nsdl_text` / `process_cdsl_text`.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import List, Optional, Tuple

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

from .extract import _is_non_latin_font

# y_top tolerance for grouping atoms into one *raw line*. Text-show ops
# on the same visual line share an identical baseline; 1.5pt absorbs
# any sub-pixel jitter without merging neighbouring lines.
Y_LINE_TOL = 1.5

# Top-to-top y-gap threshold for keeping consecutive raw lines in the
# same logical *block*. NSDL/CDSL tables use ~7pt line spacing within
# a row (multi-line ISIN+UCC cells, multi-line scheme names) and ~11pt
# between rows. 9pt cleanly separates them.
Y_BLOCK_TOL = 9.0

# SOFT HYPHEN (U+00AD). CAS generators insert it at a soft-wrap point so
# a long token (notably a 12-char ISIN) can break across two display
# lines. It carries no semantic content — it must be removed, and when
# it sits at the end of a fragment it marks a continuation that should be
# spliced onto the next fragment with no separator.
SOFT_HYPHEN = "\u00ad"

# Buffer sizes for ctypes
_TEXT_BUF_SIZE = 2048  # bytes (UTF-16LE), so up to 1023 chars per atom
_FONT_BUF_SIZE = 128


@dataclass
class Atom:
    """One text-show operation on a page.

    `stream_seq` is the position of this object in the content-stream
    walk (counting both text and non-text objects). Two text atoms
    that are *consecutive* in the stream — no PATH/IMAGE/etc. object
    between them — have `stream_seq` differing by exactly 1, which is
    how we tell PyMuPDF-style merge cases apart from same-row neighbours
    that PyMuPDF would keep separate.
    """

    x_left: float
    x_right: float
    y_top: float
    y_bot: float
    text: str
    font: str
    stream_seq: int = 0


def _read_text_obj(obj, tp_handle, buf, fname_buf) -> Tuple[str, str]:
    """Decode a text object's content and font name. Returns ('', '') if
    the object has no readable text or its font is non-Latin."""
    cc = pdfium_raw.FPDFTextObj_GetText(obj, tp_handle, buf, _TEXT_BUF_SIZE)
    # `cc` is byte length INCLUDING null terminator. Strip the null.
    text = bytes(buf)[: max(0, cc - 2)].decode("utf-16-le", errors="replace")
    if not text.strip():
        return "", ""
    font = pdfium_raw.FPDFTextObj_GetFont(obj)
    fn = pdfium_raw.FPDFFont_GetBaseFontName(font, fname_buf, _FONT_BUF_SIZE)
    fname = fname_buf.raw[: max(0, fn - 1)].decode("utf-8", errors="replace") if fn > 0 else ""
    if _is_non_latin_font(fname):
        return "", ""
    return text, fname


class _StreamCounter:
    __slots__ = ("v",)

    def __init__(self):
        self.v = 0

    def tick(self):
        self.v += 1
        return self.v


def _iter_text_objects(parent_obj_or_page, is_form: bool, counter: _StreamCounter):
    """Yield (text_object, stream_seq) tuples for every text page-object,
    recursing into Form XObjects. `counter` advances once for each
    object visited (text *and* non-text) — its value at yield time is
    that text-show op's stream position, so two atoms with consecutive
    counter values had nothing between them in the PDF's content stream.
    Bounds reported by `FPDFPageObj_GetBounds` are already in page
    coordinates regardless of nesting depth."""
    if is_form:
        n = pdfium_raw.FPDFFormObj_CountObjects(parent_obj_or_page)

        def get_obj(i):
            return pdfium_raw.FPDFFormObj_GetObject(parent_obj_or_page, i)
    else:
        n = pdfium_raw.FPDFPage_CountObjects(parent_obj_or_page)

        def get_obj(i):
            return pdfium_raw.FPDFPage_GetObject(parent_obj_or_page, i)

    for i in range(n):
        obj = get_obj(i)
        t = pdfium_raw.FPDFPageObj_GetType(obj)
        seq = counter.tick()
        if t == pdfium_raw.FPDF_PAGEOBJ_TEXT:
            yield obj, seq
        elif t == pdfium_raw.FPDF_PAGEOBJ_FORM:
            yield from _iter_text_objects(obj, is_form=True, counter=counter)


def extract_atoms(
    pdf_path: str,
    password: str,
    *,
    _doc: "Optional[pdfium.PdfDocument]" = None,
) -> List[List[Atom]]:
    """Return one list of Atoms per page (in object-index order).
    Recurses into Form XObjects (CDSL CAS PDFs nest their entire page
    inside a top-level FORM).

    When `_doc` is provided, reuse it instead of re-opening the PDF —
    the dispatcher opens the document exactly once and threads it
    through detect / parser / investor extractor.
    """
    doc = _doc if _doc is not None else pdfium.PdfDocument(pdf_path, password=password)
    pages: List[List[Atom]] = []
    left = ctypes.c_float()
    bottom = ctypes.c_float()
    right = ctypes.c_float()
    top = ctypes.c_float()
    buf = (ctypes.c_ushort * (_TEXT_BUF_SIZE // 2))()
    fname_buf = (ctypes.c_char * _FONT_BUF_SIZE)()
    for page in doc:
        page_handle = page.raw
        tp = page.get_textpage()
        tp_handle = tp.raw
        atoms: List[Atom] = []
        seen: set = set()  # dedup by (x_left, y_top, text)
        counter = _StreamCounter()
        for obj, seq in _iter_text_objects(page_handle, is_form=False, counter=counter):
            # Skip vertically-oriented text — the rotated CAS watermark
            # ("CAMSCASWS… / NSDLCASWS…") whose glyphs otherwise bleed
            # down the right-hand columns. The object matrix's glyph
            # advance vector is (a, b); |b| > |a| means a vertical run.
            mtx = pdfium_raw.FS_MATRIX()
            if pdfium_raw.FPDFPageObj_GetMatrix(obj, ctypes.byref(mtx)) and abs(mtx.b) > abs(mtx.a):
                continue
            text, fname = _read_text_obj(obj, tp_handle, buf, fname_buf)
            if not text:
                continue
            pdfium_raw.FPDFPageObj_GetBounds(
                obj,
                ctypes.byref(left),
                ctypes.byref(bottom),
                ctypes.byref(right),
                ctypes.byref(top),
            )
            xl, xr, yt, yb = left.value, right.value, top.value, bottom.value
            key = (round(xl, 1), round(yt, 1), text)
            if key in seen:
                continue
            seen.add(key)
            atoms.append(Atom(xl, xr, yt, yb, text, fname, stream_seq=seq))
        pages.append(_dedupe_overlapping(atoms))
    return pages


def _dedupe_overlapping(atoms: List[Atom]) -> List[Atom]:
    """CDSL CAS PDFs render some text twice — once for visible glyphs
    and once for the text layer used by accessibility tools. The two
    copies appear as separate page objects at slightly different
    x-positions but with identical text content. PyMuPDF folds them
    into one span; here we drop the later copy when an earlier atom
    at the same y already contains the same text within an
    overlapping x-range."""
    if not atoms:
        return []
    # Group by rounded y_top so we only compare within the same line.
    by_line: dict = {}
    for a in atoms:
        by_line.setdefault(round(a.y_top, 1), []).append(a)

    keep: List[Atom] = []
    for y, line_atoms in by_line.items():
        kept_at_y: List[Atom] = []
        for a in line_atoms:
            atxt = a.text.strip()
            is_dup = False
            for k in kept_at_y:
                ktxt = k.text.strip()
                if atxt != ktxt:
                    continue
                # x-range overlap?
                if a.x_left < k.x_right and k.x_left < a.x_right:
                    is_dup = True
                    break
            if not is_dup:
                kept_at_y.append(a)
        keep.extend(kept_at_y)
    return keep


def _cluster_raw_lines(atoms: List[Atom]) -> List[List[Atom]]:
    """Group atoms into raw lines by y_top within `Y_LINE_TOL`."""
    if not atoms:
        return []
    sorted_atoms = sorted(atoms, key=lambda a: (-a.y_top, a.x_left))
    lines: List[List[Atom]] = [[sorted_atoms[0]]]
    cur_y = sorted_atoms[0].y_top
    for a in sorted_atoms[1:]:
        if abs(a.y_top - cur_y) <= Y_LINE_TOL:
            lines[-1].append(a)
            # Stable anchor: keep the first y_top so jitter doesn't drift
        else:
            lines.append([a])
            cur_y = a.y_top
    return lines


def _cluster_blocks(raw_lines: List[List[Atom]]) -> List[List[Atom]]:
    """Merge consecutive raw lines whose top-to-top y-gap ≤ Y_BLOCK_TOL
    into one block. Returns a flat list of atoms per block."""
    if not raw_lines:
        return []
    blocks: List[List[Atom]] = [list(raw_lines[0])]
    prev_y = raw_lines[0][0].y_top
    for line in raw_lines[1:]:
        cur_y = line[0].y_top
        if prev_y - cur_y <= Y_BLOCK_TOL:
            blocks[-1].extend(line)
        else:
            blocks.append(list(line))
        prev_y = cur_y
    return blocks


# x_left tolerance for merging atoms into one vertical strip. Multi-line
# table cells (CAS scheme names, ISIN+UCC stacks) all share an identical
# x_left per text-show op; 3pt absorbs any sub-pixel jitter.
X_LEFT_TOL = 3.0

# Top-to-top y-gap allowed for vertical strip continuation. Within-cell
# stacking is ~7pt in NSDL/CDSL tables; 9pt admits that without bridging
# to the next row's atoms (which sit ~11pt below).
STRIP_VERTICAL_GAP = 9.0


# Two atoms in mid-table columns are merged into one vertical strip
# only if their *left edges* match within `X_LEFT_TOL` AND their
# *centres* drift apart by more than `CENTER_LEFT_ALIGN_TOL`. That
# second condition tells left-aligned multi-line cells (right edges
# wander with text width → centres drift) apart from centre-aligned
# multi-row column headers (e.g., CDSL "Average Total" stacked over
# "Expense Ratio" — both centred on the same column anchor → centres
# nearly identical, should NOT collapse into one cell).
#
# Atoms at the leftmost column of the page are exempt from the
# centre test: in CAS tables, column-1 stacking (ISIN+UCC, equity
# ticker+name) is always a multi-line cell, never a centre-aligned
# header. `LEFT_EDGE_X` is the cutoff x_left below which we trust the
# x_left match alone.
CENTER_LEFT_ALIGN_TOL = 1.0
LEFT_EDGE_X = 100.0


def _column_cluster(block_atoms: List[Atom]) -> List[List[Atom]]:
    """Group atoms within one block into vertical strips (multi-line
    *cells*). See module-level comments on the alignment heuristic.

    Why not x-range overlap? Some CAS PDFs render the UCC of an MF
    holding as a tiny single-digit text-show op (`'8'`) placed at the
    *units* column's x-position rather than under the ISIN. An overlap-
    based clusterer would absorb that `8` into the units cell and the
    NSDL_MF_HOLDINGS_RE regex would fail. PyMuPDF treats this lone `8`
    as its own block because it shares no left-aligned vertical
    neighbour at its x_left; we replicate that here. PROD's
    `extract_blocks` then emits it between `89,935.20` and `27.7978`
    as a separate `\\t\\t` cell — the regex matches with the UCC stuffed
    into the `folio` group, which is the same (admittedly imperfect)
    behaviour as production.
    """
    strips: List[List[Atom]] = []
    for a in sorted(block_atoms, key=lambda x: (-x.y_top, x.x_left)):
        placed = False
        a_center = (a.x_left + a.x_right) / 2
        for strip in strips:
            last = strip[-1]
            x_left_ok = abs(a.x_left - last.x_left) <= X_LEFT_TOL
            y_ok = -0.1 <= last.y_top - a.y_top <= STRIP_VERTICAL_GAP
            if not (x_left_ok and y_ok):
                continue
            # Left-edge column: trust x_left match. Mid-table columns:
            # also require centre drift so centre-aligned headers don't
            # collapse into a single cell.
            at_left_edge = a.x_left < LEFT_EDGE_X
            last_center = (last.x_left + last.x_right) / 2
            center_drifts = abs(a_center - last_center) > CENTER_LEFT_ALIGN_TOL
            if at_left_edge or center_drifts:
                strip.append(a)
                placed = True
                break
        if not placed:
            strips.append([a])
    return strips


# --- Structured block API for dedicated NSDL/CDSL parsers ---


@dataclass
class Cell:
    """A logical table cell — one column slice of a block. May span
    multiple lines vertically (e.g., a multi-line scheme name)."""

    x_left: float
    x_right: float
    y_top: float
    y_bot: float
    text: str  # multi-line cells use `\\n` internally
    atoms: List[Atom]  # the underlying text-show ops, for debugging


@dataclass
class Block:
    """A logical row block. Cells are sorted left→right by x_left."""

    page: int  # 1-indexed page number
    cells: List[Cell]

    @property
    def y_top(self) -> float:
        return max((c.y_top for c in self.cells), default=0.0)

    @property
    def y_bot(self) -> float:
        return min((c.y_bot for c in self.cells), default=0.0)

    @property
    def x_left(self) -> float:
        return min((c.x_left for c in self.cells), default=0.0)

    @property
    def x_right(self) -> float:
        return max((c.x_right for c in self.cells), default=0.0)

    def text(self) -> str:
        """Lossy single-string view (cells joined by `\\t\\t`)."""
        return "\t\t".join(c.text for c in self.cells if c.text)


def _join_column_atoms(atoms_top_down: List[Atom]) -> str:
    """Join a column's atom texts top-to-bottom into one cell string.

    Normally one line per atom, joined with `\\n`. The exception is the
    SOFT HYPHEN (U+00AD): when a fragment *ends* with one, the CAS
    generator soft-wrapped a single token across lines, so we splice the
    next fragment on directly (no newline) and drop the hyphen — this
    reconstructs ISINs like `INF179K01<SHY>WN9` that wrapped mid-token. Any
    remaining (embedded, mid-atom) soft hyphens are stripped too, so a
    single-atom `INF179K01<SHY>WN9` is normalised the same way.
    """
    pieces: List[str] = []
    continuation = False
    for atom in atoms_top_down:
        part = atom.text.strip()
        if not part:
            continue
        if continuation:
            pieces[-1] += part
        else:
            pieces.append(part)
        if pieces[-1].endswith(SOFT_HYPHEN):
            pieces[-1] = pieces[-1][:-1]
            continuation = True
        else:
            continuation = False
    return "\n".join(pieces).replace(SOFT_HYPHEN, "")


def _cells_from_block_atoms(block_atoms: List[Atom]) -> List[Cell]:
    """Run column-cluster and return `Cell` objects with bbox metadata.

    Text-show ops in adjacent columns (e.g., NSDL MF Holdings' folio
    `9013692 ` and units `1,11,359.0` separated by only 5.3pt) stay
    in their own cells so x-based column assignment can route them
    correctly.
    """
    strips = _column_cluster(block_atoms)
    strips.sort(key=lambda c: min(a.x_left for a in c))
    cells: List[Cell] = []
    for strip in strips:
        sorted_strip = sorted(strip, key=lambda a: (-a.y_top, a.x_left))
        joined = _join_column_atoms(sorted_strip)
        if not joined:
            continue
        cells.append(
            Cell(
                x_left=min(a.x_left for a in strip),
                x_right=max(a.x_right for a in strip),
                y_top=max(a.y_top for a in strip),
                y_bot=min(a.y_bot for a in strip),
                text=joined,
                atoms=sorted_strip,
            )
        )
    return cells


def blocks_from_atoms(pages: List[List[Atom]]) -> List[Block]:
    """Convert pre-extracted atoms into `Block`s. Lets a single
    `extract_atoms` call feed both the holdings parser and the
    investor extractor in one go (NSDL/CDSL)."""
    out: List[Block] = []
    for page_num, atoms in enumerate(pages, start=1):
        raw_lines = _cluster_raw_lines(atoms)
        atom_blocks = _cluster_blocks(raw_lines)
        for block_atoms in atom_blocks:
            cells = _cells_from_block_atoms(block_atoms)
            if cells:
                out.append(Block(page=page_num, cells=cells))
    return out


def extract_blocks(
    pdf_path: str,
    password: str,
    *,
    _doc: "Optional[pdfium.PdfDocument]" = None,
    _atoms: "Optional[List[List[Atom]]]" = None,
) -> List[Block]:
    """Return a flat list of `Block`s across all pages, in reading
    order (top-down per page, pages in document order). Entry point
    that dedicated NSDL/CDSL parsers consume.

    When `_atoms` is provided, skip re-extracting; the dispatcher
    extracts atoms once and feeds both the parser and the investor
    extractor.
    """
    pages = _atoms if _atoms is not None else extract_atoms(pdf_path, password, _doc=_doc)
    return blocks_from_atoms(pages)
