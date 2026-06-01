"""POC: CAMS/KFin SUMMARY CAS parser using column-based row reading.

Same architecture as `cams_detailed`, simpler schema — each scheme is
ONE row (no transactions). Schemes can wrap to one or two continuation
lines below for long names.

Row anatomy (CAMS, single line where possible):
  <folio> <ISIN> <rta_code>-<scheme name> <cost> <balance> <NAV date>
  <NAV> <market value> <RTA>

KFin uses the same fields but renders the header across 2-3 baselines
("Cost Value | Closing Unit Balance | Price | Market Value" on top,
"Folio No. | ISIN | Scheme Name | NAV Date" below).

Produces the same `POCResult` shape as `cams_detailed.parse` so output
can be diffed directly against production `casparser.read_cas_pdf`.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import List, Optional

from dateutil import parser as dateparse

from casparser.enums import CASFileType, FileType
from casparser.types import (
    CASData,
    Folio,
    Scheme,
    SchemeValuation,
    StatementPeriod,
)

from ._investor import extract_cams_kfin_investor
from ._isin import isin_search
from .cams_detailed import AMC_RE, Column, _decimal
from .extract import Char, Line, extract_pages

# -----------------------------------------------------------------------------
# Column anchors
# -----------------------------------------------------------------------------

# Header keywords used by both CAMS and KFin SUMMARY templates. We accept
# either dialect ("Unit Balance" / "Closing Unit Balance" for the units
# column, "NAV" / "Price" for per-unit value). Whichever label appears in
# the header row, we map it to a canonical column key.
SUMMARY_HEADER_LABELS = {
    "Folio",
    "No",
    "No.",
    "ISIN",
    "Scheme",
    "Name",
    "Cost",
    "Value",
    "Unit",
    "Balance",
    "Closing",
    "NAV",
    "Date",
    "Price",
    "Market",
    "Registrar",
}
SUMMARY_MIN_HITS = 5  # 5 distinct header words to call it a header block

HEADER_WINDOW_Y = 15.0  # same as DETAILED — header may span up to ~15pt y

# Column identity rules. Given the SET of words within one x-cluster of
# the header (across all baselines), pick the canonical column whose
# required tokens are all present. Tried in priority order — the first
# match wins. Some clusters have noise tokens like "(INR)" which we
# just ignore. Order of words within a cluster doesn't matter, which
# matters for KFin: it renders "Closing" + "Unit" on one baseline above
# "Balance" on another, and when sorted by x they come out interleaved.
COLUMN_RULES = [
    # (required tokens, optional tokens, canonical label, alignment)
    ({"Folio"}, {"No.", "No"}, "Folio", "left"),
    ({"ISIN"}, set(), "ISIN", "left"),
    ({"Scheme"}, {"Name"}, "Scheme", "left"),
    ({"Cost"}, {"Value"}, "Cost", "right"),
    ({"Closing", "Balance"}, {"Unit"}, "Balance", "right"),
    ({"Unit", "Balance"}, set(), "Balance", "right"),
    ({"NAV", "Date"}, set(), "NAVDate", "left"),
    ({"NAV"}, {"Value"}, "NAV", "right"),
    ({"Price"}, set(), "NAV", "right"),
    ({"Market"}, {"Value"}, "MarketValue", "right"),
    ({"Registrar"}, set(), "Registrar", "left"),
]

XCLUSTER_GAP = 7.0  # pts; gap larger than this between adjacent words
# (sorted by x, across all header baselines) starts a new cluster. KFin's
# headers have legitimate column separators as tight as ~9pt (Market →
# Registrar), so we need a smaller threshold than typical word spacing.


def _words_on_line(line: Line, min_gap: float = 1.5) -> List[tuple[str, float, float]]:
    """Split a line into words by x-gap OR by literal whitespace chars.
    CAMS SUMMARY header inserts an actual ' ' Char between "Folio" and
    "No.", so x-gap alone won't separate them."""
    cs = sorted(line.chars, key=lambda c: c.x0)
    words = []
    cur, cur_x0, cur_x1 = "", None, None
    for c in cs:
        if c.text.isspace():
            if cur:
                words.append((cur, cur_x0, cur_x1))
                cur = ""
            continue
        if cur and (c.x0 - cur_x1) > min_gap:
            words.append((cur, cur_x0, cur_x1))
            cur = ""
        if not cur:
            cur_x0 = c.x0
        cur += c.text
        cur_x1 = c.x1
    if cur:
        words.append((cur, cur_x0, cur_x1))
    return words


def detect_summary_columns(lines: List[Line], start_idx: int) -> Optional[tuple[int, List[Column]]]:
    """Find the SUMMARY table header. Same window approach as DETAILED:
    a contiguous span of lines within HEADER_WINDOW_Y pts that
    collectively contain ≥ SUMMARY_MIN_HITS distinct header keywords.

    Returns (index_of_last_line_in_header, ordered columns).
    """
    for i in range(start_idx, len(lines)):
        window = [lines[i]]
        for j in range(i + 1, len(lines)):
            if lines[i].baseline - lines[j].baseline > HEADER_WINDOW_Y:
                break
            window.append(lines[j])

        words: List[tuple[str, float, float]] = []
        for ln in window:
            words.extend(_words_on_line(ln))
        labels = {w[0] for w in words if w[0] in SUMMARY_HEADER_LABELS}
        if len(labels) >= SUMMARY_MIN_HITS and "Folio" in labels and "Scheme" in labels:
            last_idx = i + len(window) - 1
            return last_idx, _build_summary_columns(words)
    return None


def _build_summary_columns(words: List[tuple[str, float, float]]) -> List[Column]:
    """Cluster header words by x-proximity (across all baselines), then
    pick a canonical column for each cluster based on which tokens are
    present. Order of words within a cluster doesn't matter — KFin
    splits "Closing Unit Balance" across baselines so the x-sorted
    order interleaves to "Closing Balance Unit"."""
    sorted_words = sorted(words, key=lambda w: w[1])
    clusters: List[List[tuple[str, float, float]]] = []
    cur: List[tuple[str, float, float]] = []
    cur_max_x1 = 0.0
    for w in sorted_words:
        if cur and (w[1] - cur_max_x1) > XCLUSTER_GAP:
            clusters.append(cur)
            cur = []
            cur_max_x1 = 0.0
        cur.append(w)
        cur_max_x1 = max(cur_max_x1, w[2])
    if cur:
        clusters.append(cur)

    cols: List[Column] = []
    seen_labels: set[str] = set()
    for cluster in clusters:
        tokens = {w[0] for w in cluster}
        for required, optional, label, align in COLUMN_RULES:
            if required.issubset(tokens) and label not in seen_labels:
                x0 = min(w[1] for w in cluster)
                x1 = max(w[2] for w in cluster)
                cols.append(Column(label=label, x_lo=x0, x_hi=x1, alignment=align))
                seen_labels.add(label)
                break
    cols.sort(key=lambda c: c.x_lo)
    return cols


# -----------------------------------------------------------------------------
# Cell assignment (SUMMARY-specific zones)
# -----------------------------------------------------------------------------

# Numeric value widths in SUMMARY are narrower than DETAILED (cost/value
# rarely exceed 13 chars). NAVDate values extend LEFTWARD of the "NAV
# Date" label, so we treat it like a right-aligned column but with the
# label's left edge as the right edge of its zone.

NUMERIC_WIDTH = 42.0  # pts; widest expected numeric value in SUMMARY


def _summary_column_ranges(columns: List[Column]) -> List[tuple[Column, float, float]]:
    """Compute x-range per column.

    LEFT-aligned (incl. NAVDate, whose value `01-Jan-2015` extends
    further right than the `NAV Date` header label): from `x_lo-3` to
    just before the next column's zone.
    RIGHT-aligned numerics: from `x_hi - NUMERIC_WIDTH` to `x_hi+3`.
    """
    sorted_cols = sorted(columns, key=lambda c: (c.x_lo + c.x_hi) / 2)
    ranges = []
    for i, col in enumerate(sorted_cols):
        if col.alignment == "right":
            lo = col.x_hi - NUMERIC_WIDTH
            hi = col.x_hi + 3.0
        else:
            lo = col.x_lo - 3.0
            if i + 1 < len(sorted_cols):
                nxt = sorted_cols[i + 1]
                if nxt.alignment == "right":
                    hi = nxt.x_hi - NUMERIC_WIDTH
                else:
                    hi = nxt.x_lo - 3.0
            else:
                hi = float("inf")
        ranges.append((col, lo, hi))
    return ranges


def assign_summary_cells(line: Line, columns: List[Column]) -> dict[str, str]:
    ranges = _summary_column_ranges(columns)
    cells: dict[str, list[Char]] = {c.label: [] for c in columns}
    for ch in line.chars:
        x_mid = (ch.x0 + ch.x1) / 2
        for col, lo, hi in ranges:
            if lo <= x_mid < hi:
                cells[col.label].append(ch)
                break
    out = {}
    for label, chars in cells.items():
        if not chars:
            continue
        chars.sort(key=lambda c: c.x0)
        heights = sorted(c.h for c in chars)
        h_med = heights[len(heights) // 2]
        gap = max(1.5, 0.6 * h_med)
        parts, prev_x1 = [], None
        for c in chars:
            if prev_x1 is not None and (c.x0 - prev_x1) > gap:
                parts.append(" ")
            parts.append(c.text)
            prev_x1 = c.x1
        out[label] = "".join(parts).strip()
    return out


# -----------------------------------------------------------------------------
# Parsing
# -----------------------------------------------------------------------------

# Folio must have ≥6 digits — short numbers like '0' or '11' in
# disclaimer text aren't real folio numbers. Sub-account "/N" is
# optional.
FOLIO_CELL_RE = re.compile(r"^\s*(\d{6,}(?:\s*/\s*\d+)?)")
ISIN_CELL_RE = re.compile(r"(INF[A-Z0-9]{8}\d)")
SUMMARY_DATE_RE = re.compile(r"as\s+on\s+(\d{2}-[A-Za-z]{3}-\d{4})", re.I)
# Scheme cell: looks like "<RTA_CODE>-<scheme name>". RTA code is short
# alphanumeric (3-15 chars, no spaces), then dash, then more text.
SCHEME_CELL_RE = re.compile(r"^\s*([\w\s]{2,15}?)\s*-\s*(.+)$")
# Scheme cell looks like data when it starts with an alphanumeric RTA
# code (letters or digits, ≤16 chars incl. internal space), then a dash,
# then the scheme name. Examples: "D110 - DSP...", "117 IOD1G-Mirae...",
# "PP001ZG-Parag...". Disclaimer rows lack this exact prefix shape.
SCHEME_LOOKS_LIKE_DATA = re.compile(r"^\s*[A-Z0-9][\w\s]{1,15}\s*-\s*\S")
# The holdings table ends with a "Total" / "Grand Total" row (no folio,
# carrying the portfolio grand total). Everything after it is notes,
# disclaimers and the vertical watermark — none of which must bleed
# into the last scheme's name. A genuine scheme-name continuation is a
# *fragment*, never a row that begins with "Total".
SUMMARY_TOTAL_RE = re.compile(r"^\s*(?:grand\s+|sub\s+|portfolio\s+)?total\b", re.I)


def parse(
    pdf_path: str,
    password: str,
    file_type: FileType = FileType.UNKNOWN,
    *,
    _doc=None,
) -> CASData:
    pages = extract_pages(pdf_path, password, _doc=_doc)

    statement_date: Optional[str] = None
    folios: dict[str, Folio] = {}
    current_amc: Optional[str] = None
    current_folio: Optional[Folio] = None
    current_scheme: Optional[Scheme] = None
    last_columns: List[Column] = []

    for page in pages:
        header_pos = detect_summary_columns(page.lines, 0)
        if header_pos:
            header_idx, columns = header_pos
            last_columns = columns
        else:
            header_idx = -1
            columns = last_columns

        for i, line in enumerate(page.lines):
            text = line.text

            # --- statement date (single date for SUMMARY) ---
            if statement_date is None:
                if m := SUMMARY_DATE_RE.search(text):
                    statement_date = m.group(1)

            # --- AMC header (between groups of folios) ---
            if m := AMC_RE.match(text.strip()):
                current_amc = m.group(0)
                continue

            if not columns or header_idx is None or i <= header_idx:
                continue

            cells = assign_summary_cells(line, columns)
            folio_cell = cells.get("Folio", "").strip()
            # Some PDFs have folio "/0" suffix overflowing into the ISIN
            # column. Folios can also bleed into ISIN if very long. Use
            # the cell text as a hint and pull the ISIN by regex.
            isin_raw = cells.get("ISIN", "").strip()
            m_isin = ISIN_CELL_RE.search(isin_raw) or ISIN_CELL_RE.search(folio_cell)
            isin_cell = m_isin.group(1) if m_isin else ""
            # If folio was truncated because of "/0" overflow, recover
            # by taking the leading folio pattern. If no match, the cell
            # holds non-folio text (disclaimer / wrapped scheme name) —
            # clear it so we don't treat the row as a main row.
            m_folio = FOLIO_CELL_RE.match(folio_cell)
            folio_cell = m_folio.group(1).strip() if m_folio else ""
            scheme_cell = cells.get("Scheme", "").strip()
            balance_cell = cells.get("Balance", "").strip()
            nav_date_cell = cells.get("NAVDate", "").strip()
            nav_cell = cells.get("NAV", "").strip()
            value_cell = cells.get("MarketValue", "").strip()
            cost_cell = cells.get("Cost", "").strip()
            rta_cell = cells.get("Registrar", "").strip()

            # End of the holdings table: the grand-total / sub-total row
            # (no folio, "Total ..." in the scheme/leftmost zone). Drop
            # the current scheme so the trailing notes / disclaimers /
            # watermark can't be appended to its name as bogus
            # "continuations". A later real scheme row (e.g. the next
            # AMC after a sub-total) re-establishes current_scheme via
            # the is_main branch below.
            if not folio_cell and (
                SUMMARY_TOTAL_RE.match(scheme_cell) or SUMMARY_TOTAL_RE.match(text.strip())
            ):
                current_scheme = None
                continue

            # A "main" row is one that has BOTH a folio number AND a
            # scheme name that looks like "<RTA_CODE>-<name>". This
            # rejects disclaimer/footer text that happens to land
            # partly in the folio or scheme x-zones.
            is_main = (
                bool(folio_cell)
                and bool(scheme_cell)
                and bool(SCHEME_LOOKS_LIKE_DATA.match(scheme_cell))
            )
            # A genuine wrapped scheme name lands ONLY in the scheme
            # column — every numeric/date/registrar zone is empty.
            # Requiring that rejects footer rows (e.g. the total row, or
            # disclaimer lines carrying a stray amount) that happen to
            # spill text into the scheme x-zone.
            is_continuation = (
                current_scheme is not None
                and not folio_cell
                and scheme_cell
                and not nav_date_cell
                and not balance_cell
                and not nav_cell
                and not value_cell
                and not cost_cell
            )

            if is_main:
                # Finalise previous scheme implicitly (it stays in its folio).
                folio_no = folio_cell.strip()
                if folio_no not in folios:
                    folios[folio_no] = Folio(
                        folio=folio_no,
                        amc=current_amc or "UNKNOWN",
                        PAN="",
                        KYC=None,
                        PANKYC=None,
                        schemes=[],
                    )
                current_folio = folios[folio_no]

                # Split rta_code from scheme name: "D110-DSP ELSS..." →
                # code=D110, name=DSP ELSS...
                code = ""
                name = scheme_cell
                if m := SCHEME_CELL_RE.match(scheme_cell):
                    code = m.group(1).strip()
                    name = m.group(2).strip()

                balance = _decimal(balance_cell) or Decimal(0)
                nav = _decimal(nav_cell) or Decimal(0)
                cost = _decimal(cost_cell) if cost_cell else None
                market_value = _decimal(value_cell) or Decimal(0)
                isin = isin_cell or None

                # NAV date — convert to a real `date` object so Pydantic
                # doesn't try to coerce a `"01-Jan-2015"` string as if it
                # were ISO-format (which mis-parses to year 201). Default
                # to the statement date when the per-scheme NAV date cell
                # is empty.
                try:
                    if nav_date_cell:
                        nav_date = dateparse.parse(re.sub(r"[-\s]+", "-", nav_date_cell)).date()
                    elif statement_date:
                        nav_date = dateparse.parse(statement_date).date()
                    else:
                        nav_date = dateparse.parse("1970-01-01").date()
                except Exception:
                    nav_date = dateparse.parse("1970-01-01").date()

                rta_for_lookup = rta_cell or "CAMS"
                resolved_isin, amfi, scheme_type = isin_search(
                    name,
                    rta_for_lookup,
                    code,
                    isin=isin,
                )
                current_scheme = Scheme(
                    scheme=name,
                    advisor=None,
                    rta=rta_for_lookup,
                    rta_code=code,
                    isin=resolved_isin or isin,
                    amfi=amfi,
                    type=scheme_type or "N/A",
                    open=balance,
                    close=balance,
                    close_calculated=balance,
                    valuation=SchemeValuation(
                        date=nav_date,
                        nav=nav,
                        value=market_value,
                        cost=cost,
                    ),
                    transactions=[],
                )
                current_folio.schemes.append(current_scheme)
                continue

            if is_continuation:
                # Append the wrap text to the previous scheme's name.
                current_scheme.scheme = (current_scheme.scheme + " " + scheme_cell).strip()

    return CASData(
        statement_period=(
            StatementPeriod(from_=statement_date, to=statement_date)
            if statement_date
            else StatementPeriod(**{"from": "", "to": ""})
        ),
        folios=list(folios.values()),
        investor_info=extract_cams_kfin_investor(pdf_path, password, _doc=_doc),
        cas_type=CASFileType.SUMMARY,
        file_type=file_type,
    )
