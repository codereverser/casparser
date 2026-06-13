"""CAMS / KFin DETAILED CAS parser using column-based row reading.

Reads each page's transaction table by detecting column boundaries and
assigning cells per row, producing the `CASData` shape returned by
`casparser.read_cas_pdf`.

Handles:
- Multi-page, multi-AMC statements with one folio/scheme header per block
- Transaction table with the 6 standard columns (Date / Transaction /
  Amount / Units / Price / Unit Balance)
- "Opening Unit Balance", "Closing Unit Balance", "NAV on", "Valuation on"
  labeled rows
- ISIN / AMFI enrichment (via `_isin.isin_search`), nominees, Total Cost
  Value, and investor info / statement period

Known limitations:
- Multi-line transaction descriptions keep the first line only.
- Segregated portfolios are classified as `SEGREGATION` transactions but
  are not fully supported by the capital-gains module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from dateutil import parser as dateparse

from casparser.enums import CASFileType, FileType, TransactionType
from casparser.types import (
    CASData,
    Folio,
    Scheme,
    SchemeValuation,
    StatementPeriod,
    TransactionData,
)

from ._classify import (
    extract_gift_folio,
    get_parsed_scheme_name,
    get_transaction_type,
)
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


def detect_txn_columns(
    lines: List[Line], start_idx: int
) -> Optional[tuple[int, int, List[Column]]]:
    """Find the next transaction-table header at or after start_idx.

    A header is a y-window of consecutive lines (top-down) spanning ≤ HEADER_
    WINDOW_Y points and collectively containing ≥ TXN_MIN_HITS distinct
    column labels. We collect labels from the whole window so wraps like
    "Unit"/"Balance" stacked over 2 baselines or KFin's 4-baseline split
    behave the same.

    Returns (first_line_index, last_line_index, ordered columns) for the
    header window. Transaction parsing should start at last_line_index + 1;
    the [first, last] span is excluded from the scheme-header region buffer.
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
        return i, last_idx, _build_columns(all_words)
    return None


def _build_columns(words: List[tuple[str, float, float]]) -> List[Column]:
    """Map header words to Columns. Merge "Unit"+"Balance" into one column."""
    cols: List[Column] = []
    for text, x0, x1 in words:
        if text == "Unit" and ("Balance" in (w[0] for w in words)):
            # Find "Balance" with overlapping x-range
            for w_text, w_x0, w_x1 in words:
                if w_text == "Balance" and abs((w_x0 + w_x1) / 2 - (x0 + x1) / 2) < 30:
                    cols.append(Column("Unit Balance", min(x0, w_x0), max(x1, w_x1), "right"))
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
INLINE_ISIN_RE = re.compile(r"[-\s]*ISIN\s*:\s*([A-Z0-9]+)", re.I)
INLINE_ADVISOR_RE = re.compile(r"[-\s]*\(\s*Advisor\s*:\s*([^)]+?)\)", re.I)
SCHEME_HEAD_RTA_RE = re.compile(r"Registrar\s*:\s*(\S+)", re.I)
# A full MF ISIN is `INF` + 8 alphanumerics + 1 check digit (12 chars).
# Used to reject a truncated ISIN (e.g. `INF769K`) that results when the
# value wraps across the `Registrar :` label — passing a partial ISIN to
# isin_search suppresses the name+code fallback and yields no ISIN.
FULL_ISIN_RE = re.compile(r"^INF[0-9A-Z]{8}\d$")
# Same shape, unanchored — to find a complete ISIN anywhere in the
# stitched header when the labelled "ISIN: <value>" capture wraps.
ISIN_ANYWHERE_RE = re.compile(r"\bINF[0-9A-Z]{8}\d\b")
# Recognised registrar names. The RTA value is NOT reliably the first
# token after `Registrar :` — some templates interleave `(Advisor: ...)`,
# an ISIN-continuation fragment, or a rotated-watermark fragment between
# the label and the real registrar name. We pick the recognised token
# from the stitched scheme header instead.
RTA_TOKEN_RE = re.compile(r"\b(CAMS|KFINTECH|KFIN|KARVY)\b", re.I)
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
# Running-balance sign validator
# -----------------------------------------------------------------------------


def _apply_balance_sign_fix(scheme: Scheme) -> None:
    """Cross-check parsed ``units`` against the per-row ``balance``
    column and flip the sign of single-row mis-parses.

    Some KFin templates print *Reversed* rows (e.g. the Franklin
    wound-up debt schemes' ``Payment - Units Extinguished-Reversed``
    entries) with cosmetic parentheses around the units value even
    though the semantic sign is the opposite of the original. The
    running ``Unit Balance`` column is unambiguous, so we trust it
    and flip the sign (plus the matching amount, which the same
    parens convention turned negative) on any row where
    ``prev_balance + units != balance`` but
    ``prev_balance - units == balance``. After flipping we
    reclassify the type via :func:`get_transaction_type`, so a
    flip from negative-to-positive moves the row out of the default
    ``REDEMPTION`` bucket into whichever positive bucket the
    description warrants.

    Rows without ``units`` (STT / Stamp / TDS / MISC) and rows
    without a parsed ``balance`` are skipped — they can't be
    cross-checked. The helper is a no-op for transactions whose
    signs already agree with the running balance, which covers the
    overwhelming majority of CAS data.
    """
    tol = Decimal("0.005")
    prev_balance: Optional[Decimal] = scheme.open if scheme.open is not None else Decimal(0)
    for t in scheme.transactions:
        if t.units is None or t.balance is None or prev_balance is None:
            continue
        units = Decimal(str(t.units))
        balance = Decimal(str(t.balance))
        if abs((prev_balance + units) - balance) <= tol:
            prev_balance = balance
            continue
        if abs((prev_balance - units) - balance) <= tol:
            flipped_units = -units
            t.units = flipped_units
            if t.amount is not None:
                t.amount = -Decimal(str(t.amount))
            new_type, new_div = get_transaction_type(t.description, flipped_units)
            t.type = new_type.name
            t.dividend_rate = new_div
        prev_balance = balance

    # Recompute the running close_calculated using the corrected
    # signs so downstream invariant assertions reflect the fix.
    total = scheme.open if scheme.open is not None else Decimal(0)
    for t in scheme.transactions:
        if t.units is not None:
            total += Decimal(str(t.units))
    scheme.close_calculated = total


def _reconcile_balances(scheme: Scheme) -> List[str]:
    """Validate transactions against the printed running ``Unit Balance``.

    The statement carries its own checksum: every row prints the running
    unit balance *after* that transaction, so ``prev_balance + units``
    must equal the printed ``balance`` on each row (signs already
    corrected by :func:`_apply_balance_sign_fix`). When it doesn't, a row
    was dropped or garbled between the previous row and this one — the
    most dangerous failure mode, because the parse still *looks* fine.

    Returns one human-readable warning per discontinuity. On a mismatch
    we resync the running total to the statement's printed value so a
    single missing row produces one warning instead of cascading onto
    every row after it. A final closing-balance check catches a drop that
    has no later printed balance to expose it (e.g. a missing last row).

    Rows without ``units`` (STT / Stamp / TDS / MISC) leave the balance
    unchanged; rows without a printed ``balance`` can't be checked and
    are skipped.
    """
    tol = Decimal("0.005")
    warnings: List[str] = []
    label = f"{scheme.scheme!r} [{scheme.rta_code}]"
    running: Decimal = Decimal(str(scheme.open)) if scheme.open is not None else Decimal(0)
    for t in scheme.transactions:
        if t.units is not None:
            running += Decimal(str(t.units))
        if t.balance is None:
            continue
        printed = Decimal(str(t.balance))
        if abs(running - printed) > tol:
            warnings.append(
                f"{label}: unit-balance discontinuity at {t.date} ({t.type}) — "
                f"computed {running} but statement printed {printed} "
                f"(Δ={running - printed}); a transaction row may be missing "
                f"or mis-parsed"
            )
            running = printed  # trust the statement's own running total
    if scheme.close is not None:
        close = Decimal(str(scheme.close))
        if abs(running - close) > tol:
            warnings.append(
                f"{label}: closing unit balance mismatch — computed {running} but "
                f"statement printed {close} (Δ={running - close}); a transaction "
                f"row may be missing or mis-parsed"
            )
    return warnings


# Header-content markers used by `_build_scheme_from_buffer` to pick the
# region lines that form the wrapped scheme header: an annotation label
# (Registrar/Advisor/ISIN/Nominee), an advisor code (ARN-xxxx / INAxxxx),
# an RTA token (CAMS/KFINTECH/...), or a scheme-code line `<code>-...`
# whose code contains a letter. The letter-in-code rule is what separates
# a real scheme line ("128TSGPG-Axis…", "HGFG-HDFC…") from the
# investor-name, address, date-range and trailing load/disclaimer lines
# that also sit between the folio line and `Opening Unit Balance`
# ("Entry Load - NIL…" has a space before its dash; "01-Jan-1990…" has a
# digits-only leading token; an investor name has no dash at all).
_HEADER_MARKER_RE = re.compile(
    r"Registrar\s*:|Advisor\s*:|ISIN\s*:|Nominee\s+\d|\bARN-?\d+\b|\bINA\d+\b", re.I
)
# Scheme codes may contain internal spaces ("127 CPGPG-Motilal…"), so the
# rule is: alphanumeric tokens, at least one letter somewhere (lookahead),
# and the dash glued to the last token — "Entry Load - NIL" (space before
# dash) and "01-Jan-1990" (digits-only code) both fail.
_SCHEME_CODE_RE = re.compile(r"^\s*(?=[A-Z0-9 ]{0,40}[A-Z])[A-Z0-9]+(?: [A-Z0-9]+)*-", re.I)
# A trailing, unfinished marker means the value continues on the next
# document line, which may carry no marker of its own (e.g. "(Advisor:
# Registrar : CAMS" wrapping to "ARN-28283)", or a bare "WEALTH)"
# distributor code) — the member that follows a dangling line joins the
# header regardless of its own content.
_TRAILING_MARKER_RE = re.compile(r"(Registrar\s*:|Advisor\s*:|ISIN\s*:|\(\s*Advisor\s*:)\s*$", re.I)
# Scheme-name cleanup happens in two passes, mirroring the old parser:
#
# 1. EXCISE annotations that templates splice into the *middle* of the
#    name — a closed `(Advisor: …)` and a valued `- ISIN: INFxxx` — so
#    text on BOTH sides survives. CAMS IDCW templates routinely emit
#    `… - IDCW - ISIN: INF090I01155 - Payout (Advisor:…)`: cutting at
#    the first annotation would amputate the `- Payout` / `- Reinvest`
#    qualifier that distinguishes the two IDCW variants.
# 2. CUT at the first marker that genuinely terminates the name —
#    `Registrar`, a nominee label, an RTA token, a bare ARN/INA code
#    (an advisor value that wrapped outside its parens), or a dangling
#    `(Advisor:` opener (necessarily unclosed after pass 1).
#
# A valueless `ISIN:` (its value wrapped behind the `Registrar :` label)
# is deliberately NOT excised or cut: the old parser let it ride into
# `get_parsed_scheme_name`'s trailing-punctuation cleanup, and name-level
# output compatibility matters more than cosmetics here.
_NAME_EXCISE_ISIN_RE = re.compile(r"[-\s]*ISIN\s*:\s*INF[A-Z0-9]*", re.I)
_NAME_TERMINATOR_RE = re.compile(
    r"\(\s*Advisor\s*:"
    r"|Registrar\s*:?"
    r"|Nominee\s+\d"
    r"|\bARN-?\d+\b|\bINA\d+\b"
    r"|\b(?:CAMS|KFINTECH|KFIN|KARVY)\b",
    re.I,
)
_ADVISOR_CODE_RE = re.compile(r"\b(ARN-?\d+|INA\d+)\b", re.I)


def _is_header_line(text: str) -> bool:
    return bool(
        _HEADER_MARKER_RE.search(text) or RTA_TOKEN_RE.search(text) or _SCHEME_CODE_RE.match(text)
    )


def _expects_continuation(text: str) -> bool:
    """True if `text` leaves a marker value dangling onto the next line."""
    if _TRAILING_MARKER_RE.search(text.strip()):
        return True
    adv = re.search(r"\(\s*Advisor\s*:", text, re.I)
    return bool(adv) and ")" not in text[adv.end() :]


def _find_scheme_line(lines: List[str]) -> Optional[int]:
    """Index of the region's `<code>-<name>` scheme line, or ``None``.

    "ARN" is a distributor's AMFI registration prefix, never a scheme
    RTA code — an `ARN-28283)` advisor-value wrap line is not a scheme.
    """
    for k, ln in enumerate(lines):
        if not _SCHEME_CODE_RE.match(ln):
            continue
        if ln.partition("-")[0].strip().upper() == "ARN":
            continue
        return k
    return None


def _header_member_indices(lines: List[str]) -> List[int]:
    """Indices of the region lines that form the stitched scheme header.

    A line joins if it carries header content (`_is_header_line`) or if
    the previous member left a marker value dangling
    (`_expects_continuation`). Everything else — load/disclaimer text,
    dates, addresses — is ignored; junk in the region is harmless
    because it never joins the stitched text.
    """
    out: List[int] = []
    forced = False
    for idx, ln in enumerate(lines):
        if forced or _is_header_line(ln):
            out.append(idx)
            forced = _expects_continuation(ln)
        else:
            forced = False
    return out


def _region_candidate(buf: List[str]) -> Optional[tuple[List[str], int, List[int], str]]:
    """Locate a scheme-header candidate in a region buffer.

    Returns ``(lines, scheme_line_index, member_indices, header_text)``
    when the region holds a scheme line AND ``Registrar`` evidence in
    its stitched member text — the shared gate for building a scheme
    and for warning about a region that was discarded unbuilt. ``None``
    means the region is routine junk (trailing load / disclaimer text):
    a hyphenated word can look like a ``<code>-`` line, but it never
    comes with a ``Registrar`` label.
    """
    lines = [s.strip() for s in buf if s.strip()]
    if not lines:
        return None
    s_idx = _find_scheme_line(lines)
    if s_idx is None:
        return None
    members = _header_member_indices(lines)
    header_text = " ".join(lines[k] for k in members)
    if "Registrar" not in header_text:
        return None
    return lines, s_idx, members, header_text


def _build_scheme_from_buffer(
    buf: List[str], statement_period: Optional[StatementPeriod]
) -> Optional[Scheme]:
    """Build a :class:`Scheme` from an accumulated scheme-header region.

    ``buf`` holds *every* line between the folio line / previous
    scheme's footer and this scheme's ``Opening Unit Balance`` (minus
    recognised anchors and the transaction-column-header window), in
    document order. The accumulation loop is deliberately dumb; all
    judgment lives here, where the whole region is visible at once:

    1. locate the ``<code>-<name>`` scheme line;
    2. select the member lines that form the wrapped header — marker
       lines, RTA tokens, and dangling-value continuations;
    3. extract each field independently with unanchored searches over
       the stitched member text. There is no single do-everything
       header regex, so a stray region line can pollute at most the
       one field whose pattern it happens to match.

    The scheme *name* is the only positional field: the scheme line's
    text after ``<code>-`` plus any member lines that follow it, cut at
    the first annotation marker.

    Returns the built :class:`Scheme`, or ``None`` when the region has
    no scheme line or no ``Registrar`` evidence (every known template
    carries the label somewhere in the header; without it a ``<code>-``
    line is a footnote, not a scheme).
    """
    cand = _region_candidate(buf)
    if cand is None:
        return None
    lines, s_idx, members, header_text = cand

    code, _, scheme_rest = lines[s_idx].partition("-")
    code = code.strip()
    name_text = " ".join([scheme_rest] + [lines[k] for k in members if k > s_idx])
    # Excise mid-name annotations first (both sides survive), then cut
    # at the first true name terminator. See the regex comments above.
    name_text = INLINE_ADVISOR_RE.sub("", name_text)
    name_text = _NAME_EXCISE_ISIN_RE.sub("", name_text)
    cut = _NAME_TERMINATOR_RE.search(name_text)
    raw_name = name_text[: cut.start()] if cut else name_text
    name = get_parsed_scheme_name(raw_name)

    isin_m = INLINE_ISIN_RE.search(header_text)
    inline_isin = isin_m.group(1).strip() if isin_m else None
    # The value after "ISIN:" can wrap, so the labelled capture may grab
    # the following "Registrar" label or a truncated stub. If it isn't a
    # full ISIN, look for a complete one anywhere in the stitched header.
    if not inline_isin or not FULL_ISIN_RE.match(inline_isin):
        anywhere = ISIN_ANYWHERE_RE.search(header_text)
        inline_isin = anywhere.group(0) if anywhere else None

    adv_m = INLINE_ADVISOR_RE.search(header_text)
    advisor = adv_m.group(1).strip() if adv_m else None
    # When the advisor value wrapped below an interleaved "Registrar :
    # <RTA>", the captured blob looks like "Registrar : CAMS ARN-28283" —
    # narrow it to the actual distributor (ARN-xxxx) / RIA (INAxxxx) code.
    if advisor and (adv_code := _ADVISOR_CODE_RE.search(advisor)):
        advisor = adv_code.group(1)

    # The registrar name is not reliably the first token after
    # `Registrar :` — prefer the recognised RTA token anywhere in the
    # stitched header, then the first post-label token (covers RTAs
    # outside the known set, e.g. FTAMIL), then CAMS.
    if rta_m := RTA_TOKEN_RE.search(header_text):
        rta = rta_m.group(1).upper()
    else:
        label_m = SCHEME_HEAD_RTA_RE.search(header_text)
        rta = (label_m.group(1).strip() if label_m else "") or "CAMS"
    isin, amfi, scheme_type = isin_search(name, rta, code, isin=inline_isin)
    # Nominees are matched per region line, not on the joined blob, because
    # NOMINEE_RE is `$`-anchored — the nominee text must sit at end-of-line.
    nominees: List[str] = []
    for ln in lines:
        if nm := NOMINEE_RE.search(ln):
            noms = [
                (nm.group("n1") or "").strip(),
                (nm.group("n2") or "").strip(),
                (nm.group("n3") or "").strip(),
            ]
            nominees = [n for n in noms if n]
            break
    return Scheme(
        scheme=name,
        advisor=advisor,
        rta=rta,
        rta_code=code,
        isin=isin,
        amfi=amfi,
        type=scheme_type or "N/A",
        nominees=nominees,
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


def _abandoned_region_warning(buf: List[str], where: str) -> Optional[str]:
    """Warning text for an open header region discarded at ``where``
    without its ``Opening Unit Balance`` anchor — that scheme and all of
    its transactions are silently skipped otherwise. Reported only when
    the region passes the same candidate gate as scheme building, so the
    routine junk after a folio's last scheme footer stays quiet."""
    cand = _region_candidate(buf)
    if cand is None:
        return None
    lines, s_idx, _, _ = cand
    return (
        f"scheme header region discarded at {where} without an 'Opening Unit "
        f"Balance' anchor; its scheme and transactions were skipped: {lines[s_idx][:80]!r}"
    )


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
    # Keyed by (amc, folio_no): folio numbers are RTA-scoped, not globally
    # unique, so two AMCs can share one. Keying on the number alone would
    # silently merge the second AMC's schemes into the first AMC's folio.
    folios: dict[tuple[str, str], Folio] = {}
    current_amc: Optional[str] = None
    current_folio: Optional[Folio] = None
    current_scheme: Optional[Scheme] = None
    last_columns: List[Column] = []  # inherited if current page lacks header

    # Scheme-header region accumulator. The header is the only part of the
    # grammar that wraps unpredictably; everything else (folio line, Opening
    # /Closing Unit Balance, NAV/Valuation footer) is a single, never-wrapping
    # anchor. So instead of stitching adjacent lines by proximity, we collect
    # every line of the region (folio line / previous footer → next Opening
    # Unit Balance) into `header_buf` and parse it once. Declared outside the
    # page loop so a header split across a page break still accumulates.
    header_buf: List[str] = []
    header_active: bool = False

    # Non-fatal data-quality warnings. Region anomalies (an unparseable
    # or abandoned header) are appended during the loop; the per-scheme
    # balance reconciliation extends the list afterwards.
    parse_warnings: List[str] = []

    for page in pages:
        header_pos = detect_txn_columns(page.lines, 0)
        if header_pos:
            col_first, header_idx, columns = header_pos
            last_columns = columns
        else:
            # Continuation page — no header. Inherit from previous.
            # header_idx=-1 means transactions can start from line 0; the
            # empty [col_first, header_idx] window then excludes nothing.
            col_first = header_idx = -1
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
                # An AMC boundary ends any dangling header region —
                # loudly, if it still held an unconsumed scheme line.
                if header_active and (w := _abandoned_region_warning(header_buf, "AMC boundary")):
                    parse_warnings.append(w)
                header_buf = []
                header_active = False
                continue

            # --- Folio header ---
            # A genuine folio header is its own line. A transaction row that
            # *mentions* a folio number in its description — e.g.
            # "Gifting of units-TO Folio No: 12345678901" — also contains
            # "Folio No:" and matches FOLIO_LINE_RE, but always starts with a
            # transaction date. Reject dated rows so gift transfers are not
            # mistaken for folio boundaries (which dropped the row and, when a
            # scheme's own folio number was redacted, the whole scheme).
            if (
                "Folio No" in text
                and not DATE_CELL_RE.match(text)
                and (m := FOLIO_LINE_RE.search(text))
            ):
                # Preserve internal " / " for compatibility with production
                # parser output format (it keeps "12124203 / 63" style).
                folio_no = m.group(1).strip()
                folio_key = (current_amc or "UNKNOWN", folio_no)
                if folio_key not in folios:
                    folios[folio_key] = Folio(
                        folio=folio_no,
                        amc=current_amc or "UNKNOWN",
                        PAN=m.group(2) or "",
                        KYC=m.group(3) or None,
                        PANKYC=m.group(4) or None,
                        schemes=[],
                    )
                current_folio = folios[folio_key]
                current_scheme = None
                # The lines until this folio's first Opening Unit Balance
                # are its first scheme's header region.
                if header_active and (w := _abandoned_region_warning(header_buf, "folio boundary")):
                    parse_warnings.append(w)
                header_buf = []
                header_active = True
                continue

            # --- Opening Unit Balance: closes the scheme-header region and
            #     builds the scheme from the accumulated buffer. ---
            if m := OPEN_BAL_RE.search(text):
                if header_active:
                    current_scheme = _build_scheme_from_buffer(header_buf, statement_period)
                    if current_scheme is not None:
                        current_folio.schemes.append(current_scheme)
                    else:
                        # An Opening Unit Balance implies a scheme header
                        # above it; failing to parse one means this whole
                        # scheme — transactions included — is dropped.
                        # Never let that happen silently.
                        region = [s.strip() for s in header_buf if s.strip()]
                        snippet = " / ".join(region[:2])[:120]
                        parse_warnings.append(
                            f"unparseable scheme header region before 'Opening Unit "
                            f"Balance' on page {page.number}; the scheme and its "
                            f"transactions were skipped: {snippet!r}"
                        )
                    header_active = False
                    header_buf = []
                if current_scheme is not None:
                    current_scheme.open = _decimal(m.group(1)) or Decimal(0)
                    current_scheme.close_calculated = current_scheme.open
                continue

            # --- Footer rows (attach to the just-closed scheme). `Closing
            #     Unit Balance` re-opens the region for the next scheme; the
            #     NAV / Valuation / Cost lines that follow are this scheme's
            #     footer and are consumed here, never buffered. Nominee lines
            #     belong to the *next* scheme's header, so while a region is
            #     open they fall through to the buffer and are extracted in
            #     `_build_scheme_from_buffer`. ---
            consumed_footer = False
            if current_scheme is not None:
                if m := CLOSE_BAL_RE.search(text):
                    current_scheme.close = _decimal(m.group(1)) or Decimal(0)
                    header_buf = []
                    header_active = True
                    consumed_footer = True
                if m := NAV_RE.search(text):
                    current_scheme.valuation.date = dateparse.parse(m.group(1)).date()
                    current_scheme.valuation.nav = _decimal(m.group(2)) or Decimal(0)
                    consumed_footer = True
                if m := VALUATION_RE.search(text):
                    current_scheme.valuation.date = dateparse.parse(m.group(1)).date()
                    current_scheme.valuation.value = _decimal(m.group(2)) or Decimal(0)
                    consumed_footer = True
                if m := COST_VALUE_RE.search(text):
                    current_scheme.valuation.cost = _decimal(m.group(1))
                    consumed_footer = True
                if not header_active and (m := NOMINEE_RE.search(text)):
                    noms = [
                        (m.group("n1") or "").strip(),
                        (m.group("n2") or "").strip(),
                        (m.group("n3") or "").strip(),
                    ]
                    current_scheme.nominees = [n for n in noms if n]

            # --- Scheme-header region accumulation. Reached only while a
            #     region is open; skip the footer anchors just consumed and
            #     the transaction-column-header window, buffer everything
            #     else. Deliberately judgment-free: which lines form the
            #     header is decided in `_build_scheme_from_buffer`, where
            #     the whole region is visible at once. ---
            if header_active:
                if not consumed_footer and not (col_first <= i <= header_idx):
                    header_buf.append(text)
                continue

            if current_scheme is None:
                continue

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
                gift_folio = (
                    extract_gift_folio(desc)
                    if txn_type in (TransactionType.GIFT_IN, TransactionType.GIFT_OUT)
                    else None
                )
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
                        gift_folio=gift_folio,
                    )
                )

    # A region still open at end-of-document with a scheme line inside
    # means the closing anchor never arrived — report, don't swallow.
    if header_active and (w := _abandoned_region_warning(header_buf, "end of document")):
        parse_warnings.append(w)

    # Cross-check each scheme's transactions against the running
    # `Unit Balance` column and fix cosmetic-parens sign mis-parses
    # (e.g. KFin Franklin `Payment - Units Extinguished-Reversed`
    # rows). Cheap, self-validating, and a no-op when signs already
    # agree with the printed balance.
    # Then reconcile each scheme against its printed running Unit Balance
    # (the statement's own checksum) and surface any discontinuity as a
    # non-fatal warning — the cheapest possible signal for the otherwise
    # silent "a row was dropped / mis-parsed" failure mode.
    for folio in folios.values():
        for scheme in folio.schemes:
            _apply_balance_sign_fix(scheme)
            parse_warnings.extend(_reconcile_balances(scheme))

    return CASData(
        statement_period=statement_period or StatementPeriod(**{"from": "", "to": ""}),
        folios=list(folios.values()),
        investor_info=extract_cams_kfin_investor(pdf_path, password, _doc=_doc),
        cas_type=CASFileType.DETAILED,
        file_type=file_type,
        parse_warnings=parse_warnings,
    )
