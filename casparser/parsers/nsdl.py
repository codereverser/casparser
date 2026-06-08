"""Dedicated NSDL parser.

Consumes structured `Block`/`Cell` data from `pageobj.extract_blocks`
and produces an `NSDLCASData` directly — no detour through PROD's
`process_nsdl_text` and its regex tokenisation.

Source-of-truth strategy:

  1. **Page 2 carries the canonical account roster** in a small summary
     table — one row per demat / MF-folio account with type, broker,
     DP ID, Client ID, ISIN/scheme count, and balance. We bootstrap
     accounts from here. This avoids the "false header" pollution that
     comes from scanning every page for the phrase "Demat Account"
     (footers, footnotes, paragraph 6/10/etc. all mention it).

  2. **Per-account section headers** match an account in the roster
     by `(dp_id, client_id)` and become the cursor for subsequent
     equity / MF / bond rows in that section.

  3. **"Mutual Fund Folios (F)" detailed table** routes to the
     MF-folio pseudo-account. Rows use x-position-anchored columns
     so the misplaced lone-digit UCC NSDL occasionally renders in
     the units column gets recognised as an anomaly and folded back
     into `ucc`, not into the numeric fields.

Decimals are parsed at the parser level, stripping Indian-format
commas, so we don't depend on the `MutualFund.fix_float` validator
(which has a bug where aliased fields with `Optional[Decimal]`
annotation slip past comma-stripping).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

from casparser.enums import FileType
from casparser.types import (
    Bond,
    DematAccount,
    DematOwner,
    Equity,
    MutualFund,
    NSDLCASData,
    StatementPeriod,
)

from . import pageobj
from ._investor import extract_nsdl_cdsl_investor
from .pageobj import Block, Cell

# --- patterns ---

ISIN_RE = re.compile(r"^[A-Z]{2}[0-9A-Z]{9}\d$")
INF_ISIN_RE = re.compile(r"^INF[0-9A-Z]{8}\d$")
INE_ISIN_RE = re.compile(r"^IN[E9][0-9A-Z]{8}\d$")
# Some templates drop the leading zero on fractional values (e.g. a
# `0.196` unit balance prints as `.196`), so the integer part is
# optional when a decimal part is present.
NUMERIC_RE = re.compile(r"^-?(?:[\d,]+(?:\.\d+)?|\.\d+)$")

PERIOD_RE = re.compile(
    r"(?:for\s+the\s+period\s+from|statement\s+for\s+the\s+period\s+from)\s+"
    r"(\d{2}-[A-Za-z]{3}-\d{4})\s+to\s+(\d{2}-[A-Za-z]{3}-\d{4})",
    re.I,
)

DEMAT_TYPE_RE = re.compile(r"^(NSDL|CDSL)\s+Demat\s+Account\s*$", re.I)
DP_CLIENT_RE = re.compile(
    r"DP\s*ID\s*:?\s*(\S+?)\s+Client\s*ID\s*:?\s*(\d+)",
    re.I,
)
PAN_RE = re.compile(r"(.+?)\s*\(PAN\s*:\s*([^)]+)\)", re.I)
MF_FOLIOS_HEADER_RE = re.compile(r"^Mutual\s+Fund\s+Folios\b", re.I)


# --- decimal helpers ---


def _to_decimal(text) -> Decimal:
    if text is None:
        return Decimal(0)
    s = str(text).replace(",", "").strip()
    if not s or s in ("-", "--", "N.A", "NA"):
        return Decimal(0)
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal(0)


def _opt_decimal(text) -> Optional[Decimal]:
    if text is None:
        return None
    s = str(text).replace(",", "").strip()
    if not s or s in ("-", "--", "N.A", "NA"):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _looks_numeric(text: str) -> bool:
    s = text.strip()
    if not s:
        return False
    return bool(NUMERIC_RE.match(s))


# --- column anchors (x_left ranges) for the detailed MF Holdings table ---


@dataclass(frozen=True)
class _MFHoldingsCols:
    # x_left bands for the 10 columns of the detailed MF Holdings
    # table. Each band must not overlap its neighbour AND must cover
    # the full range a cell's `x_left` can drift across — PDFium
    # rounds glyph positions slightly differently across fixtures, so
    # bands are deliberately wider than the visual column width
    # (gap-to-gap rather than glyph-to-glyph). The "units" band reaches
    # ~260 because the units glyph in some NSDL CAS variants starts at
    # x≈225 and right-aligns; without the wider window the units cell
    # falls into the inter-column gap and reads as 0.
    cols = (
        ("isin_ucc", 15, 75),
        ("name", 75, 150),
        ("folio", 150, 200),
        ("units", 200, 260),
        ("avg_cost", 260, 310),
        ("total_cost", 310, 375),
        ("current_nav", 375, 425),
        ("current_value", 425, 480),
        ("pnl", 480, 555),
        ("returns", 555, 600),
    )

    def assign(self, cell: Cell) -> Optional[str]:
        for key, lo, hi in self.cols:
            if lo <= cell.x_left < hi:
                return key
        return None


_MF_HOLDINGS = _MFHoldingsCols()


# --- column anchors for the summary Corporate-Bonds table ---
#
# The NSDL-flavour bonds table on a demat-account page renders 8 data
# cells per row. Two of them ('frequency' text + 'coupon rate' numeric)
# share an x-band so they can't be distinguished by x alone — see
# `_parse_bond_summary_row` for how the text/numeric discriminator is
# applied within that band.
@dataclass(frozen=True)
class _BondSummaryCols:
    cols = (
        ("isin", 15, 80),
        ("name", 80, 175),
        ("coupon_band", 175, 240),  # frequency (text) + coupon (numeric)
        ("maturity", 240, 310),
        ("num_bonds", 310, 390),
        ("face_value", 390, 510),
        ("value", 510, 600),
    )

    def assign(self, cell: Cell) -> Optional[str]:
        for key, lo, hi in self.cols:
            if lo <= cell.x_left < hi:
                return key
        return None


_BOND_SUMMARY = _BondSummaryCols()


# --- account key utilities ---


def _full_type(type_word: str) -> str:
    """`NSDL`/`CDSL` -> `NSDL Demat Account`/`CDSL Demat Account` to
    match the convention used elsewhere in the codebase."""
    return f"{type_word.upper()} Demat Account"


def _account_key(type_word: str, dp_id: str, client_id: str) -> Tuple[str, str, str]:
    return (type_word.upper(), dp_id.strip(), client_id.strip())


# --- parser entry point ---


def parse_nsdl(
    pdf_path: str,
    password: str,
    file_type: FileType = FileType.NSDL,
    *,
    _doc=None,
) -> NSDLCASData:
    # Extract atoms once, then derive both the structured Blocks the
    # holdings parser needs and the investor info from the same pages.
    atoms = pageobj.extract_atoms(pdf_path, password, _doc=_doc)
    blocks = pageobj.blocks_from_atoms(atoms)
    period = _find_period(blocks) or StatementPeriod(**{"from": "", "to": ""})

    # Phase 1: bootstrap accounts from page-2 summary table.
    accounts_by_key: Dict[Tuple[str, str, str], DematAccount] = {}
    ordered_accounts: List[DematAccount] = []
    mf_folios_account: Optional[DematAccount] = None

    pending_owners: List[DematOwner] = []  # owners harvested from
    # the most recent 'in the (single|joint) name of' header; consumed
    # by the next summary-demat row and reset on each new header.

    for b in blocks:
        if b.page != 2:
            continue
        txt = b.text()
        ltxt = txt.lower()
        if (
            "in the single name of" in ltxt
            or "in the joint names of" in ltxt
            or "in the joint name of" in ltxt
        ):
            pending_owners = []
            continue
        # Capture owner names that follow.
        if PAN_RE.search(txt):
            # `name1 (PAN:...)\nname2 (PAN:...)` is one cell with \n.
            for m in PAN_RE.finditer(txt):
                pending_owners.append(
                    DematOwner(
                        name=m.group(1).strip(),
                        PAN=m.group(2).strip(),
                    )
                )
            continue
        # Summary demat-account row
        if _is_summary_demat_row(b):
            ac, key = _account_from_summary_row(b, list(pending_owners))
            if key not in accounts_by_key:
                accounts_by_key[key] = ac
                ordered_accounts.append(ac)
            continue
        # Summary MF Folios row
        if _is_summary_mf_folios_row(b):
            if mf_folios_account is None:
                mf_folios_account = _mf_folios_account_from_summary(b, list(pending_owners))
                ordered_accounts.append(mf_folios_account)
            continue

    # Phase 2: walk all blocks; identify per-account section headers
    # and parse subsequent holdings into the matching account.
    #
    # `cur_section` (equities / mfunds / bonds) is set by the small
    # section-marker blocks ('Equity Shares', 'Mutual Funds (M)',
    # 'Corporate Bonds (C)' etc.) and disambiguates the otherwise-
    # identical 18-cell detailed-table header — without it, MF and
    # bond rows on a CDSL detailed page get misrouted into equities.
    # `cur_mode` is the final routing key (one of
    # 'equities_summary', 'equities_detailed', 'mfunds_summary',
    # 'mfunds_detailed', 'mf_holdings', 'bonds_summary',
    # 'bonds_detailed') chosen by `_detect_mode_from_header` once the
    # column-header row arrives.
    page_blocks = [b for b in blocks if b.page > 2]
    cur_account: Optional[DematAccount] = None
    cur_mode: Optional[str] = None
    cur_section: Optional[str] = None

    i = 0
    while i < len(page_blocks):
        b = page_blocks[i]
        txt = b.text()
        ltxt = txt.lower()

        # Per-account section header. Same-block form (single-name
        # accounts) or split across 3 blocks (joint-name accounts).
        ac_key, consumed = _try_per_account_header(page_blocks, i)
        if ac_key is not None:
            cur_account = accounts_by_key.get(ac_key)
            cur_mode = None
            cur_section = None
            i += consumed
            continue

        # MF Folios detailed-table header
        if "mutual fund folios (f)" in ltxt:
            cur_account = mf_folios_account
            cur_mode = "mf_holdings"
            cur_section = "mfunds"
            i += 1
            continue

        # Table-header rows tell us which kind of holdings table follows.
        if cur_account is not None:
            mode = _detect_mode_from_header(b, cur_section)
            if mode is not None:
                cur_mode = mode
                i += 1
                continue
            if _is_total_row(b):
                i += 1
                continue
            sec = _section_marker_kind(b)
            if sec is not None:
                cur_section = sec
                # Don't clear cur_mode here — for unsupported sections
                # (preference shares, AIF, etc.) we want subsequent
                # rows to fall through and be ignored. cur_mode is
                # cleared/reset when the next table header is seen.
                cur_mode = None
                i += 1
                continue

        # Holdings rows
        if cur_account is None or cur_mode is None:
            i += 1
            continue
        if cur_mode == "equities_summary":
            eq = _parse_equity_row(b, detailed=False)
            if eq:
                cur_account.equities.append(eq)
        elif cur_mode == "equities_detailed":
            eq = _parse_equity_row(b, detailed=True)
            if eq:
                cur_account.equities.append(eq)
        elif cur_mode == "mfunds_summary":
            mf = _parse_summary_mf_row(b)
            if mf:
                cur_account.mutual_funds.append(mf)
        elif cur_mode == "mfunds_detailed":
            mf = _parse_detailed_mf_row(b)
            if mf:
                cur_account.mutual_funds.append(mf)
        elif cur_mode == "mf_holdings":
            mf = _parse_mf_holdings_row(b)
            if mf:
                cur_account.mutual_funds.append(mf)
        elif cur_mode == "bonds_summary":
            bd = _parse_bond_summary_row(b)
            if bd:
                cur_account.bonds.append(bd)
        elif cur_mode == "bonds_detailed":
            bd = _parse_bond_detailed_row(b)
            if bd:
                cur_account.bonds.append(bd)
        i += 1

    return NSDLCASData(
        statement_period=period,
        accounts=ordered_accounts,
        investor_info=extract_nsdl_cdsl_investor(
            pdf_path,
            password,
            _atoms=atoms,
        ),
        file_type=file_type,
    )


# --- summary-row recognisers (page 2) ---


def _is_summary_demat_row(block: Block) -> bool:
    """Page-2 summary row. Two physical layouts produce the same logical
    row depending on how PDFium clusters the broker name and DP/Client
    line:

      4-cell form: ``Type | "<BROKER>\\nDP ID:... Client ID:..." | folios | value``
        Broker name and DP-ID line share one cell (joined with newline).

      5-cell form: ``Type | "<BROKER>" | "DP ID:... Client ID:..." | folios | value``
        Broker name and DP-ID line render as separate cells.

    Both are accepted; we discriminate by locating the cell whose text
    contains the DP/Client pattern.
    """
    if len(block.cells) not in (4, 5):
        return False
    if not DEMAT_TYPE_RE.match(block.cells[0].text.strip()):
        return False
    return any(DP_CLIENT_RE.search(c.text) for c in block.cells[1:])


def _is_summary_mf_folios_row(block: Block) -> bool:
    if len(block.cells) != 4:
        return False
    if not MF_FOLIOS_HEADER_RE.match(block.cells[0].text.strip()):
        return False
    return True


def _account_from_summary_row(
    block: Block, owners: List[DematOwner]
) -> Tuple[DematAccount, Tuple[str, str, str]]:
    """Build a DematAccount from a page-2 summary row, handling both the
    4-cell and 5-cell layouts (see :func:`_is_summary_demat_row`)."""
    type_word = DEMAT_TYPE_RE.match(block.cells[0].text.strip()).group(1).upper()
    # Locate the cell carrying the DP/Client identifiers; everything
    # before it (cell index 1, possibly inline in the same cell) is the
    # broker name.
    dp_cell_idx = next(
        (i for i in range(1, len(block.cells)) if DP_CLIENT_RE.search(block.cells[i].text)),
        1,
    )
    dp_cell_text = block.cells[dp_cell_idx].text
    dpc = DP_CLIENT_RE.search(dp_cell_text)
    dp_id = dpc.group(1) if dpc else ""
    client_id = dpc.group(2) if dpc else ""
    # Broker = the dp-cell text minus the DP/Client suffix, falling
    # back to the cell immediately before it for the 5-cell layout.
    broker_lines = [
        ln.strip() for ln in dp_cell_text.split("\n") if ln.strip() and not DP_CLIENT_RE.search(ln)
    ]
    if broker_lines:
        broker = broker_lines[0]
    elif dp_cell_idx >= 2:
        broker = block.cells[dp_cell_idx - 1].text.strip()
    else:
        broker = ""
    # Numerics (folios, balance) are the last two cells.
    folios = int(_to_decimal(block.cells[-2].text))
    balance = _to_decimal(block.cells[-1].text)
    ac = DematAccount(
        name=broker,
        type=_full_type(type_word),
        dp_id=dp_id,
        client_id=client_id,
        folios=folios,
        balance=balance,
        owners=list(owners),
        equities=[],
        mutual_funds=[],
        bonds=[],
    )
    return ac, _account_key(type_word, dp_id, client_id)


def _mf_folios_account_from_summary(block: Block, owners: List[DematOwner]) -> DematAccount:
    # cells: ['Mutual Fund Folios', '25 Folios', '44', '5,37,10,359.39']
    folios_m = re.search(r"(\d+)", block.cells[1].text)
    folios = int(folios_m.group(1)) if folios_m else 0
    balance = _to_decimal(block.cells[3].text)
    return DematAccount(
        name="Mutual Fund Folios",
        type="Mutual Fund Folios",
        dp_id="",
        client_id="",
        folios=folios,
        balance=balance,
        owners=list(owners),
        equities=[],
        mutual_funds=[],
        bonds=[],
    )


# --- per-account section headers ---


def _try_per_account_header(
    blocks: List[Block], i: int
) -> Tuple[Optional[Tuple[str, str, str]], int]:
    """A per-account header marks the start of a holdings section for
    a specific account. Returns `(account_key, blocks_consumed)`.

    Two header layouts:
      A) **Single-block (single-name account)** — one block has TYPE
         + DP/Client in one row of 5 cells, plus broker name +
         'ACCOUNT HOLDER' + PAN.
      B) **Split (joint-name account)** — the header spans three
         consecutive blocks: `<TYPE> Demat Account | ACCOUNT HOLDERS`,
         `<BROKER> | <NAME1> (PAN:…)`, `DP ID:… Client ID:… | <NAME2>
         (PAN:…)`. We scan ahead up to 3 blocks to gather the DP/Client.

    Footnotes / paragraph text that happens to mention 'demat account'
    are rejected because they have too many cells / too little
    structure.
    """
    b = blocks[i]
    txt = b.text()
    type_m = re.search(r"\b(NSDL|CDSL)\b\s+Demat\s+Account", txt, re.I)
    if not type_m:
        return None, 1

    # Case A: DP/Client in the same block
    dpc = DP_CLIENT_RE.search(txt)
    if dpc and 3 <= len(b.cells) <= 8 and len(txt) < 500:
        return _account_key(type_m.group(1), dpc.group(1), dpc.group(2)), 1

    # Case B: look ahead for DP/Client (joint-account header form)
    if "account holders" in txt.lower() or "account holder" in txt.lower():
        for j in range(1, 4):
            if i + j >= len(blocks):
                break
            nxt = blocks[i + j]
            if nxt.page != b.page:
                break
            dpc = DP_CLIENT_RE.search(nxt.text())
            if dpc:
                return _account_key(type_m.group(1), dpc.group(1), dpc.group(2)), j + 1

    return None, 1


def _detect_mode_from_header(
    block: Block,
    cur_section: Optional[str] = None,
) -> Optional[str]:
    """Return the holdings-mode this column-header row implies, or
    None if it isn't a header row.

    `cur_section` (set by the most recent section-marker block) is used
    to disambiguate the 18-cell detailed table header — its column set
    is identical for equities, mutual funds and bonds, and only the
    preceding section marker tells us which.
    """
    if re.search(r"\b(IN[EF9][0-9A-Z]{8}\d)\b", block.text(), re.I):
        return None  # has an ISIN → it's a data row
    txt = block.text().lower().replace("\n", " ").replace("\t\t", " ")
    # MF Holdings (F) — must check before the simpler "folio no" guard
    # since this header also carries "ISIN Description" and "Folio No.".
    if "folio no" in txt and ("average" in txt or "total cost" in txt):
        return "mf_holdings"
    # Detailed CDSL/NSDL holdings table — identical column set for
    # equities / mutual funds / bonds; disambiguate by section.
    if "current bal" in txt and ("market price" in txt or "value in" in txt):
        if cur_section == "bonds":
            return "bonds_detailed"
        if cur_section == "mfunds":
            return "mfunds_detailed"
        return "equities_detailed"
    # Summary bonds table on a demat-account page.
    if "coupon" in txt and ("maturity" in txt or "frequency" in txt):
        return "bonds_summary"
    # Summary equity table.
    if "stock symbol" in txt and "company name" in txt:
        return "equities_summary"
    # Summary MF table on per-account page.
    if "isin description" in txt and ("nav" in txt or "value in" in txt):
        return "mfunds_summary"
    return None


# Section markers are short blocks (1-2 cells) whose text labels which
# kind of holdings the following table contains.  Mapping is from the
# lowercased marker text to a section name used to disambiguate detailed
# table headers.  Sections not in the map ('preference shares (p)',
# 'alternate investment fund (a)', etc.) are still recognised as
# markers — they clear the active mode so subsequent rows are ignored
# — but route their rows to no holdings list.
_SECTION_MARKER_MAP = {
    "equity shares": "equities",
    "equities (e)": "equities",
    "mutual funds (m)": "mfunds",
    "mutual funds units held with the amc": "mfunds",
    "corporate bonds (c)": "bonds",
}

# Markers we recognise as 'a new section starts here' but whose rows
# we don't parse — keeps cur_mode cleared so unrelated subsequent rows
# don't get misrouted into the previous section's list.
_UNSUPPORTED_SECTION_MARKERS = frozenset(
    {
        "preference shares (p)",
        "alternate investment fund (a)",
        "money market instruments (i)",
        "securitised instruments (s)",
        "government securities (g)",
        "postal saving scheme (o)",
        "national pension system (n)",
        "zero coupon zero principal(z)",
    }
)


def _section_marker_kind(block: Block) -> Optional[str]:
    """Return the section label ('equities' / 'mfunds' / 'bonds' /
    'unsupported') if `block` is a section marker, else None."""
    if len(block.cells) > 2:
        return None
    txt = block.text().strip().lower()
    if txt in _SECTION_MARKER_MAP:
        return _SECTION_MARKER_MAP[txt]
    if txt in _UNSUPPORTED_SECTION_MARKERS:
        return "unsupported"
    return None


# --- generic recognisers ---


def _find_period(blocks: List[Block]) -> Optional[StatementPeriod]:
    for b in blocks:
        m = PERIOD_RE.search(b.text())
        if m:
            return StatementPeriod(**{"from": m.group(1), "to": m.group(2)})
    return None


def _is_table_header(block: Block) -> bool:
    """Column-label row (no ISIN, multiple recognisable header words)."""
    txt = block.text().lower().replace("\t\t", " ").replace("\n", " ")
    if re.search(r"\b(IN[EF9][0-9A-Z]{8}\d)\b", txt, re.I):
        return False
    keywords = (
        "isin description",
        "no. of\nunits",
        "no. of\nshares",
        "stock symbol",
        "current bal",
        "free bal",
        "market price",
        "value in",
        "total cost",
        "current nav",
        "unrealised",
        "annualised",
        "isin description folio",
        "isin description no.",
    )
    return sum(1 for k in keywords if k in txt) >= 2


def _is_total_row(block: Block) -> bool:
    first = block.cells[0].text.strip().lower() if block.cells else ""
    return first in ("sub total", "total", "grand total")


# --- equity row ---


def _parse_equity_row(block: Block, detailed: bool = False) -> Optional[Equity]:
    """Equity row. Cell 0 carries the ISIN (sometimes with ticker on a
    second line). Trailing cells are numerics.

    - **Summary form** (NSDL-account 'Equity Shares' table): 4
      numerics — face_value, num_shares, price, value. We take the
      last three.
    - **Detailed form** (CDSL-account / extended-NSDL table): 11
      numerics — current_bal (=num_shares), free, lent, pledge_setup,
      locked_in, safekeep, earmarked, pledged, pledgee, market_price,
      value. We take numerics[0] for num_shares and the last two for
      price / value.
    """
    if not block.cells:
        return None
    first = block.cells[0].text
    first_token = first.split("\n", 1)[0].strip()
    if not ISIN_RE.match(first_token):
        return None
    isin = first_token

    name_cell = block.cells[1].text.replace("\n", " ").strip() if len(block.cells) > 1 else None

    numerics = [c.text.strip() for c in block.cells[2:] if _looks_numeric(c.text)]
    if len(numerics) < 3:
        return None
    if detailed or len(numerics) >= 5:
        num_shares = _to_decimal(numerics[0])
    else:
        num_shares = _to_decimal(numerics[-3])
    price = _to_decimal(numerics[-2])
    value = _to_decimal(numerics[-1])

    return Equity(
        name=name_cell,
        isin=isin,
        num_shares=num_shares,
        price=price,
        value=value,
    )


# --- summary MF row (per-account 'Mutual Funds (M)' table) ---


def _parse_summary_mf_row(block: Block) -> Optional[MutualFund]:
    if not block.cells:
        return None
    first = block.cells[0].text.strip()
    if not ISIN_RE.match(first):
        return None
    isin = first
    name = block.cells[1].text.replace("\n", " ").strip() if len(block.cells) > 1 else None
    numerics = [c.text.strip() for c in block.cells[2:] if _looks_numeric(c.text)]
    if len(numerics) < 3:
        return None
    balance = _to_decimal(numerics[0])
    nav = _to_decimal(numerics[1])
    value = _to_decimal(numerics[2])
    return MutualFund(
        name=name,
        isin=isin,
        balance=balance,
        nav=nav,
        value=value,
    )


# --- detailed MF Holdings row ---


def _parse_mf_holdings_row(block: Block) -> Optional[MutualFund]:
    """Detailed holdings row: ISIN, UCC, scheme name, folio, 7 numerics.
    Cells map to columns by x-position. Out-of-band cells (e.g., the
    lone UCC `8` PDFium renders at the units column's x position) are
    flagged as anomalies and folded into the UCC field if missing."""
    if not block.cells:
        return None
    by_col: Dict[str, Cell] = {}
    anomalies: List[Cell] = []
    for cell in block.cells:
        key = _MF_HOLDINGS.assign(cell)
        if key is None or key in by_col:
            anomalies.append(cell)
        else:
            by_col[key] = cell

    if "isin_ucc" not in by_col:
        return None
    isin_cell = by_col["isin_ucc"].text
    lines = [ln.strip() for ln in isin_cell.split("\n") if ln.strip()]
    if not lines or not INF_ISIN_RE.match(lines[0]):
        return None
    isin = lines[0]
    ucc: Optional[str] = lines[1] if len(lines) > 1 else None

    PLACEHOLDER_UCCS = {"NOT AVAILABLE", "NA", "N.A.", ""}
    needs_ucc = ucc is None or ucc.upper() in PLACEHOLDER_UCCS
    if anomalies and needs_ucc:
        for a in anomalies:
            t = a.text.strip()
            if t and len(t) <= 32:
                ucc = t
                break

    name = by_col["name"].text.replace("\n", " ").strip() if "name" in by_col else None
    folio = by_col["folio"].text.strip() if "folio" in by_col else None

    balance = _to_decimal(by_col.get("units").text if "units" in by_col else None)
    avg_cost = _opt_decimal(by_col.get("avg_cost").text if "avg_cost" in by_col else None)
    total_cost = _opt_decimal(by_col.get("total_cost").text if "total_cost" in by_col else None)
    nav = _to_decimal(by_col.get("current_nav").text if "current_nav" in by_col else None)
    value = _to_decimal(by_col.get("current_value").text if "current_value" in by_col else None)
    pnl = _opt_decimal(by_col.get("pnl").text if "pnl" in by_col else None)
    ret = _opt_decimal(by_col.get("returns").text if "returns" in by_col else None)

    return MutualFund(
        name=name,
        isin=isin,
        balance=balance,
        nav=nav,
        value=value,
        avg_cost=avg_cost,
        total_cost=total_cost,
        ucc=ucc,
        folio=folio,
        pnl=pnl,
        **{"return": ret},
    )


# --- detailed MF row (CDSL-style 'Mutual Funds (M)' table) ---


def _parse_detailed_mf_row(block: Block) -> Optional[MutualFund]:
    """Detailed 'Mutual Funds (M)' row on a CDSL demat-account page.

    Same 18-column header / 13-cell data row as detailed equities;
    ISINs start with `INF` rather than `INE`. We surface `num_shares`
    as `balance` and the last two numerics as `nav` / `value`.
    """
    if not block.cells:
        return None
    first = block.cells[0].text.strip()
    if not INF_ISIN_RE.match(first):
        return None
    isin = first
    name = block.cells[1].text.replace("\n", " ").strip() if len(block.cells) > 1 else None
    numerics = [c.text.strip() for c in block.cells[2:] if _looks_numeric(c.text)]
    if len(numerics) < 3:
        return None
    balance = _to_decimal(numerics[0])
    nav = _to_decimal(numerics[-2])
    value = _to_decimal(numerics[-1])
    return MutualFund(
        name=name,
        isin=isin,
        balance=balance,
        nav=nav,
        value=value,
    )


# --- bond rows ---


def _parse_bond_summary_row(block: Block) -> Optional[Bond]:
    """NSDL-flavour summary bonds row (8 data cells).

    Cells map to columns by x-position via `_BOND_SUMMARY`. Two cells
    share the 'coupon_band' x range — the textual one is the coupon
    frequency ("Once a year", "On Maturity") and the numeric one is the
    coupon rate (e.g. 8.10). They're discriminated by ``_looks_numeric``.
    """
    if not block.cells:
        return None
    first = block.cells[0].text.split("\n", 1)[0].strip()
    if not ISIN_RE.match(first):
        return None
    isin = first

    by_col: Dict[str, List[Cell]] = {}
    for cell in block.cells:
        key = _BOND_SUMMARY.assign(cell)
        if key is None:
            continue
        by_col.setdefault(key, []).append(cell)

    name = None
    if "name" in by_col:
        name = " ".join(c.text.replace("\n", " ").strip() for c in by_col["name"]).strip() or None

    coupon_rate: Optional[Decimal] = None
    coupon_frequency: Optional[str] = None
    for c in by_col.get("coupon_band", []):
        if _looks_numeric(c.text):
            coupon_rate = _opt_decimal(c.text)
        else:
            txt = c.text.replace("\n", " ").strip()
            if txt:
                coupon_frequency = txt

    maturity_date: Optional[str] = None
    if "maturity" in by_col:
        maturity_date = by_col["maturity"][0].text.strip() or None

    num_bonds = _to_decimal(by_col["num_bonds"][0].text) if "num_bonds" in by_col else Decimal(0)
    face_value = _opt_decimal(by_col["face_value"][0].text) if "face_value" in by_col else None
    value = _to_decimal(by_col["value"][0].text) if "value" in by_col else Decimal(0)

    return Bond(
        name=name,
        isin=isin,
        num_bonds=num_bonds,
        value=value,
        face_value=face_value,
        coupon_rate=coupon_rate,
        coupon_frequency=coupon_frequency,
        maturity_date=maturity_date,
    )


def _parse_bond_detailed_row(block: Block) -> Optional[Bond]:
    """CDSL-flavour detailed bonds row (13 data cells, same layout as
    detailed equities). Yields only `num_bonds`, `market_price` and
    `value` — the detailed table doesn't carry coupon / maturity /
    face-value information.
    """
    if not block.cells:
        return None
    first = block.cells[0].text.strip()
    if not ISIN_RE.match(first):
        return None
    isin = first
    name = block.cells[1].text.replace("\n", " ").strip() if len(block.cells) > 1 else None
    numerics = [c.text.strip() for c in block.cells[2:] if _looks_numeric(c.text)]
    if len(numerics) < 3:
        return None
    num_bonds = _to_decimal(numerics[0])
    market_price = _opt_decimal(numerics[-2])
    value = _to_decimal(numerics[-1])
    return Bond(
        name=name,
        isin=isin,
        num_bonds=num_bonds,
        value=value,
        market_price=market_price,
    )
