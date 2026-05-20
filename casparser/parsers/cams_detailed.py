"""POC: CAMS DETAILED CAS parser using column-based row reading.

Produces the same `List[Folio]` shape as the production parser so output can
be diffed directly. ISIN/AMFI enrichment and investor info are deferred —
those passes are orthogonal to the column-reader question.

Scope of this POC (handles):
- One CAS, possibly multi-page
- One AMC, one folio header per folio, one scheme header per scheme
- Transaction table with 6 standard columns (Date / Transaction / Amount /
  Units / Price / Unit Balance)
- "Opening Unit Balance", "Closing Unit Balance", "NAV on", "Valuation on"
  labeled rows

Deferred (TODO markers below):
- Multi-line transaction descriptions (we keep first line only)
- ISIN / AMFI lookup
- Nominees
- Segregated portfolios
- Total Cost Value parsing
- Investor info / statement period
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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
    TransactionData,
)

from ._classify import get_parsed_scheme_name, get_transaction_type
from ._investor import extract_cams_kfin_investor
from ._isin import isin_search
from .extract import Char, Line, extract_pages

# -----------------------------------------------------------------------------
# Column anchors
# -----------------------------------------------------------------------------

# CAMS DETAILED transaction table. The header is two physical rows in the
# PDF: "Date Transaction Amount Units Price Unit" on top, "(INR) (INR)
# Balance" below. We require ≥4 of these labels on one line.
TXN_HEADER_LABELS = {"Date", "Transaction", "Amount", "Units", "Price", "Unit", "Balance", "NAV"}
TXN_MIN_HITS = 4

# All numeric columns are right-aligned; Date and Transaction are left-aligned.
ALIGN = {
    "Date": "left",
    "Transaction": "left",
    "Amount": "right",
    "Units": "right",
    "Price": "right",
    "Unit Balance": "right",
    "NAV": "right",
}


@dataclass
class Column:
    label: str
    x_lo: float  # range covering header label width
    x_hi: float
    alignment: str  # 'left' | 'right'

    @property
    def x_anchor(self) -> float:
        """For right-aligned columns, x_hi is the snap target; for left,
        x_lo is."""
        return self.x_hi if self.alignment == "right" else self.x_lo


def _words_on_line(line: Line, min_gap: float = 1.5) -> List[tuple[str, float, float]]:
    """Return [(text, x0, x1)] words on a line, splitting on x-gap > min_gap."""
    cs = sorted(line.chars, key=lambda c: c.x0)
    words = []
    cur, cur_x0, cur_x1 = "", None, None
    for c in cs:
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


HEADER_WINDOW_Y = 15.0  # vertical span (pts) that constitutes one logical
# header block. CAMS uses 2 baselines spanning ~10pt; KFin uses 4 baselines
# spanning ~11pt (Amount/Price at top, Unit, Date/Transaction/Units, (INR)
# /(INR)/Balance at bottom).


def detect_txn_columns(lines: List[Line], start_idx: int) -> Optional[tuple[int, List[Column]]]:
    """Find the next transaction-table header at or after start_idx.

    A header is a y-window of consecutive lines (top-down) spanning ≤ HEADER_
    WINDOW_Y points and collectively containing ≥ TXN_MIN_HITS distinct
    column labels. We collect labels from the whole window so wraps like
    "Unit"/"Balance" stacked over 2 baselines or KFin's 4-baseline split
    behave the same.

    Returns (index_of_last_line_in_header, ordered columns). Transaction
    parsing should start at index + 1.
    """
    for i in range(start_idx, len(lines)):
        window = [lines[i]]
        for j in range(i + 1, len(lines)):
            if lines[i].baseline - lines[j].baseline > HEADER_WINDOW_Y:
                break
            window.append(lines[j])

        all_words: List[tuple[str, float, float]] = []
        for line in window:
            all_words.extend(_words_on_line(line))
        labels = {w[0] for w in all_words if w[0] in TXN_HEADER_LABELS}
        if len(labels) < TXN_MIN_HITS:
            continue
        last_idx = i + len(window) - 1
        return last_idx, _build_columns(all_words)
    return None


def _build_columns(words: List[tuple[str, float, float]]) -> List[Column]:
    """Map header words to Columns. Merge "Unit"+"Balance" into one column."""
    cols: List[Column] = []
    used = set()
    for text, x0, x1 in words:
        if text == "Unit" and ("Balance" in (w[0] for w in words)):
            # Find "Balance" with overlapping x-range
            for w_text, w_x0, w_x1 in words:
                if w_text == "Balance" and abs((w_x0 + w_x1) / 2 - (x0 + x1) / 2) < 30:
                    cols.append(Column("Unit Balance", min(x0, w_x0), max(x1, w_x1), "right"))
                    used.add(id((text, x0, x1)))
                    used.add(id((w_text, w_x0, w_x1)))
                    break
        elif text in ALIGN and text not in ("Unit", "Balance"):
            cols.append(Column(text, x0, x1, ALIGN[text]))
    cols.sort(key=lambda c: c.x_lo)
    return cols


NUMERIC_ZONE_WIDTH = 55.0  # pts; right-aligned numeric values sit within
# this width to the left of the column's x_hi. Wide enough for any common
# Indian-format amount (e.g. "1,23,45,678.90") but narrow enough to exclude
# wrapped description text that bleeds in from the left.


def _column_ranges(columns: List[Column]) -> List[tuple[Column, float, float]]:
    """Compute x-range per column. Right-aligned numeric columns get a
    fixed-width zone ending at x_hi. Left-aligned columns extend from x_lo to
    the start of the next column's zone.

    The fundamental asymmetry: description text (Transaction column) is wide
    and naturally extends into the Amount column's x-space, while the actual
    amount value is in a narrow zone right-aligned to x_hi. Hence numeric
    columns are bounded by content-width, not by midpoint to neighbors.
    """
    sorted_cols = sorted(columns, key=lambda c: (c.x_lo + c.x_hi) / 2)
    ranges: List[tuple[Column, float, float]] = []
    for i, col in enumerate(sorted_cols):
        if col.alignment == "right":
            lo = col.x_hi - NUMERIC_ZONE_WIDTH
            hi = col.x_hi + 3.0
        else:
            lo = col.x_lo - 3.0
            if i + 1 < len(sorted_cols):
                nxt = sorted_cols[i + 1]
                hi = nxt.x_hi - NUMERIC_ZONE_WIDTH if nxt.alignment == "right" else nxt.x_lo - 3.0
            else:
                hi = float("inf")
        ranges.append((col, lo, hi))
    return ranges


def assign_cells(line: Line, columns: List[Column]) -> dict[str, str]:
    """Bucket each char into a column by x-midpoint, then render each cell
    text in left-to-right order. Overlay duplicates are already filtered
    upstream by ``extract.extract_pages`` at the atom level."""
    ranges = _column_ranges(columns)
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
# Label parsers (folio/scheme/labeled rows)
# -----------------------------------------------------------------------------

FOLIO_LINE_RE = re.compile(
    # Folio format: <digits> with optional " / <digits>" sub-account
    # suffix. Spaces around the slash are common in the source PDF.
    # Each of PAN / KYC / PAN-KYC is optional but when present
    # appears in this order on the same line. `.*?` lives *inside*
    # the optional group so a non-greedy match doesn't skip past it
    # and leave the capture empty.
    r"Folio\s+No\s*:\s*(\d+(?:\s*/\s*\d+)?)"
    r"(?:.*?PAN\s*:\s*([A-Z]{5}\d{4}[A-Z]))?"
    r"(?:.*?KYC\s*:\s*(OK|NOT OK))?"
    r"(?:.*?PAN\s*:\s*(OK|NOT OK))?",
    re.I,
)
SCHEME_HEAD_RE = re.compile(
    # `<CODE>-<NAME> Registrar:<RTA>`. The `<NAME>` chunk may carry
    # inline `(Advisor: <ARN>)` and `- ISIN: <ISIN>` segments in either
    # order — newer KFin templates put `(Advisor:...) - ISIN:...`,
    # newer CAMS templates put `- ISIN: ...(Advisor: ...)`. We capture
    # everything between code and Registrar as `name` and then strip
    # the advisor / ISIN fragments out in a second pass.
    r"^(?P<code>[\w\s]+?)-\s*(?P<name>.+?)" r"\s+Registrar\s*:\s*(?P<rta>\S+)",
    re.I,
)
INLINE_ISIN_RE = re.compile(r"[-\s]*ISIN\s*:\s*([A-Z0-9]+)", re.I)
INLINE_ADVISOR_RE = re.compile(r"[-\s]*\(\s*Advisor\s*:\s*([^)]+?)\)", re.I)
SCHEME_HEAD_RTA_RE = re.compile(r"Registrar\s*:\s*(\S+)", re.I)
OPEN_BAL_RE = re.compile(r"Opening\s+Unit\s+Balance\s*:?\s*([\d,.]+)", re.I)
CLOSE_BAL_RE = re.compile(r"Closing\s+Unit\s+Balance\s*:?\s*([\d,.]+)", re.I)
NAV_RE = re.compile(r"NAV\s+on\s+(\d{2}-[A-Za-z]{3}-\d{4})\s*:\s*INR\s*([\d,.]+)", re.I)
VALUATION_RE = re.compile(
    r"(?:Valuation|Market\s+Value)\s+on\s+(\d{2}-[A-Za-z]{3}-\d{4})\s*:\s*INR\s*([\d,.]+)",
    re.I,
)
COST_VALUE_RE = re.compile(r"Total\s+Cost\s+Value\s*:?\s*([\d,.]+)", re.I)
# Nominee block on the folio header. Three optional name slots; an
# empty slot ("Nominee 2: ") means no nominee at that position.
NOMINEE_RE = re.compile(
    r"Nominee\s+1\s*:\s*(?P<n1>[^:]*?)\s*(?:Nominee\s+2\s*:\s*(?P<n2>[^:]*?)\s*"
    r"(?:Nominee\s+3\s*:\s*(?P<n3>.*?))?)?$",
    re.I,
)
STMT_PERIOD_RE = re.compile(
    r"(\d{2}-[A-Za-z]{3}-\d{4})\s+To\s+(\d{2}-[A-Za-z]{3}-\d{4})",
    re.I,
)
# AMC header line. Most issuers end in "Mutual Fund" or "MF"; a few
# newer entrants use "<X> Fund House" instead. We anchor on the
# trailing suffix so disclaimer paragraphs that happen to mention an
# AMC name mid-sentence don't get classified as section headers.
AMC_RE = re.compile(
    r"^(.+?\s+(?:MF|Mutual\s*Fund|Fund\s*House))$",
    re.I,
)
# Extract leading date pattern. Accept "25-Oct-2021", "25 Oct 2021",
# "25Oct2021", etc. Dashes sometimes sit on a different baseline. The
# regex anchors only at start so it survives stray trailing chars
# (e.g. KFin's instalment number "1" leaking from the description column).
DATE_CELL_RE = re.compile(r"^\s*(\d{1,2}[-\s]*[A-Za-z]{3}[-\s]*\d{4})")


def _decimal(s: str) -> Optional[Decimal]:
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    neg = s.startswith("(") or s.startswith("-")
    s = s.lstrip("(").rstrip(")").lstrip("-").replace(",", "")
    try:
        d = Decimal(s)
        return -d if neg else d
    except Exception:
        return None


# -----------------------------------------------------------------------------
# Top-level parse
# -----------------------------------------------------------------------------


def parse(
    pdf_path: str,
    password: str,
    file_type: FileType = FileType.UNKNOWN,
    *,
    _doc=None,
) -> CASData:
    pages = extract_pages(pdf_path, password, _doc=_doc)

    statement_period: Optional[StatementPeriod] = None
    folios: dict[str, Folio] = {}
    current_amc: Optional[str] = None
    current_folio: Optional[Folio] = None
    current_scheme: Optional[Scheme] = None
    last_columns: List[Column] = []  # inherited if current page lacks header

    for page in pages:
        header_pos = detect_txn_columns(page.lines, 0)
        if header_pos:
            header_idx, columns = header_pos
            last_columns = columns
        else:
            # Continuation page — no header. Inherit from previous.
            # header_idx=-1 means transactions can start from line 0.
            header_idx = -1
            columns = last_columns

        for i, line in enumerate(page.lines):
            text = line.text

            # --- statement period (first page only) ---
            if statement_period is None:
                if m := STMT_PERIOD_RE.search(text):
                    statement_period = StatementPeriod(from_=m.group(1), to=m.group(2))

            # --- AMC ---
            if m := AMC_RE.match(text.strip()):
                current_amc = m.group(0)
                continue

            # --- Folio header ---
            if "Folio No" in text and (m := FOLIO_LINE_RE.search(text)):
                # Preserve internal " / " for compatibility with production
                # parser output format (it keeps "12124203 / 63" style).
                folio_no = m.group(1).strip()
                if folio_no not in folios:
                    folios[folio_no] = Folio(
                        folio=folio_no,
                        amc=current_amc or "UNKNOWN",
                        PAN=m.group(2) or "",
                        KYC=m.group(3) or None,
                        PANKYC=m.group(4) or None,
                        schemes=[],
                    )
                current_folio = folios[folio_no]
                current_scheme = None
                continue

            # --- Scheme header ---
            # The scheme block can span up to 3 baselines depending on AMC
            # and statement template:
            #
            #   Older CAMS:                            Newer CAMS:
            #   <code>-<name> ... Registrar : CAMS    Registrar :
            #   WEALTH)                                <code>-<name> ... (Advisor:...)
            #                                          KFINTECH
            #
            # We stitch up to 2 lines above and 2 lines below the
            # current line (within Y_BAND pts y-distance) if those
            # adjacent lines contain Registrar / Advisor / ISIN markers
            # or look like the standalone RTA value (CAMS / KFINTECH).
            Y_BAND = 5.0
            if current_folio is not None and "-" in text:
                parts_above = []
                parts_below = []
                base_y = page.lines[i].baseline
                for offset in (1, 2):
                    j = i - offset
                    if j < 0:
                        break
                    if page.lines[j].baseline - base_y > Y_BAND:
                        break
                    t_above = page.lines[j].text.strip()
                    if re.fullmatch(r"Registrar\s*:?", t_above, re.I) or re.search(
                        r"Registrar\s*:|Advisor\s*:|ISIN\s*:", t_above, re.I
                    ):
                        parts_above.insert(0, t_above)
                # When the scheme line ENDS with an incomplete trailing
                # marker (e.g. "(Advisor: Registrar :"), take the next
                # baseline below as the value continuation regardless of
                # its content — the value tokens (ARN-XYZ, INAxxxxx,
                # CAMS, KFINTECH) don't all match a fixed pattern.
                trailing_incomplete = bool(
                    re.search(
                        r"(Registrar\s*:|Advisor\s*:|ISIN\s*:|\(\s*Advisor\s*:)\s*$",
                        text.strip(),
                        re.I,
                    )
                )
                for offset in (1, 2):
                    j = i + offset
                    if j >= len(page.lines):
                        break
                    if base_y - page.lines[j].baseline > Y_BAND:
                        break
                    t_below = page.lines[j].text.strip()
                    if (
                        re.fullmatch(r"(CAMS|KFINTECH|KFIN)\)?", t_below, re.I)
                        or re.search(r"Registrar\s*:|Advisor\s*:|ISIN\s*:", t_below, re.I)
                        or (offset == 1 and trailing_incomplete)
                    ):
                        parts_below.append(t_below)
                # Scheme line FIRST so SCHEME_HEAD_RE can anchor to `<code>-`.
                # Then append annotations from any direction.
                scheme_text = " ".join([text.strip()] + parts_above + parts_below)
                # Trailing "Registrar :" with value already on the next
                # token after stitching → ensure value present.
                if scheme_text.endswith("Registrar :") or scheme_text.endswith("Registrar:"):
                    if i + 1 < len(page.lines):
                        toks = page.lines[i + 1].text.split()
                        if toks:
                            scheme_text = scheme_text + " " + toks[0]
                if "Registrar" in scheme_text and (m := SCHEME_HEAD_RE.match(scheme_text)):
                    code = m.group("code").strip()
                    raw_name = m.group("name")
                    # Pull `(Advisor: …)` and `- ISIN: …` out of name
                    # (templates emit them in either order). Capture
                    # values first, then `re.sub` both fragments so we
                    # don't have to track shifted span offsets.
                    isin_m = INLINE_ISIN_RE.search(raw_name)
                    inline_isin = isin_m.group(1).strip() if isin_m else None
                    adv_m = INLINE_ADVISOR_RE.search(raw_name)
                    advisor = adv_m.group(1).strip() if adv_m else None
                    raw_name = INLINE_ISIN_RE.sub("", raw_name)
                    raw_name = INLINE_ADVISOR_RE.sub("", raw_name)
                    name = get_parsed_scheme_name(raw_name)
                    rta = (m.group("rta") or "").strip() or "CAMS"
                    isin, amfi, scheme_type = isin_search(
                        name,
                        rta,
                        code,
                        isin=inline_isin,
                    )
                    current_scheme = Scheme(
                        scheme=name,
                        advisor=advisor,
                        rta=rta,
                        rta_code=code,
                        isin=isin,
                        amfi=amfi,
                        type=scheme_type or "N/A",
                        open=Decimal(0),
                        close=Decimal(0),
                        close_calculated=Decimal(0),
                        valuation=SchemeValuation(
                            date=statement_period.to if statement_period else "1970-01-01",
                            nav=Decimal(0),
                            value=Decimal(0),
                        ),
                        transactions=[],
                    )
                    current_folio.schemes.append(current_scheme)
                    continue

            if current_scheme is None:
                continue

            # --- Labeled rows ---
            if m := OPEN_BAL_RE.search(text):
                current_scheme.open = _decimal(m.group(1)) or Decimal(0)
                current_scheme.close_calculated = current_scheme.open
                continue
            if m := CLOSE_BAL_RE.search(text):
                current_scheme.close = _decimal(m.group(1)) or Decimal(0)
            if m := NAV_RE.search(text):
                current_scheme.valuation.date = dateparse.parse(m.group(1)).date()
                current_scheme.valuation.nav = _decimal(m.group(2)) or Decimal(0)
            if m := VALUATION_RE.search(text):
                current_scheme.valuation.date = dateparse.parse(m.group(1)).date()
                current_scheme.valuation.value = _decimal(m.group(2)) or Decimal(0)
            if m := COST_VALUE_RE.search(text):
                current_scheme.valuation.cost = _decimal(m.group(1))
            if m := NOMINEE_RE.search(text):
                noms = [
                    (m.group("n1") or "").strip(),
                    (m.group("n2") or "").strip(),
                    (m.group("n3") or "").strip(),
                ]
                current_scheme.nominees = [n for n in noms if n]

            # --- Transaction row (only when we have columns AND we're past
            #     the header block on this page) ---
            if columns and header_idx is not None and i > header_idx:
                cells = assign_cells(line, columns)
                date_str = cells.get("Date", "").strip()
                desc = cells.get("Transaction", "").strip()
                m_date = DATE_CELL_RE.match(date_str)
                if not m_date:
                    continue
                if not desc:
                    continue  # row with date but no description: skip
                date_str = m_date.group(1)
                # Normalize: collapse runs of dashes/spaces from overlay
                # bleed-through, e.g. "15--Jan--2021" -> "15-Jan-2021".
                date_str = re.sub(r"[-\s]+", "-", date_str).strip("-")
                amt = _decimal(cells.get("Amount", ""))
                units = _decimal(cells.get("Units", ""))
                nav = _decimal(cells.get("Price", "") or cells.get("NAV", ""))
                bal = _decimal(cells.get("Unit Balance", ""))
                # A row with no amount AND no units is not a real transaction
                # (usually a stray date in a footnote like "Effective from
                # 01-Apr-2019…"). Skip these.
                if amt is None and units is None:
                    continue
                # Some older CAMS / KFin templates omit the per-row Price
                # column for transactions but always carry Amount + Units.
                # Derive `nav = amount / units` so downstream capital-gains
                # FIFO calculations don't crash on `nav=None`.
                if nav is None and amt is not None and units is not None and units != 0:
                    nav = (amt / units).quantize(Decimal("0.0001"))
                txn_type, dividend_rate = get_transaction_type(desc, units)
                if units is not None:
                    current_scheme.close_calculated += units
                current_scheme.transactions.append(
                    TransactionData(
                        date=dateparse.parse(date_str).date(),
                        description=desc,
                        amount=amt,
                        units=units,
                        nav=nav,
                        balance=bal,
                        type=txn_type.name,
                        dividend_rate=dividend_rate,
                    )
                )

    return CASData(
        statement_period=statement_period or StatementPeriod(**{"from": "", "to": ""}),
        folios=list(folios.values()),
        investor_info=extract_cams_kfin_investor(pdf_path, password, _doc=_doc),
        cas_type=CASFileType.DETAILED,
        file_type=file_type,
    )
