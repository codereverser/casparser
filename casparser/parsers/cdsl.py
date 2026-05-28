"""Dedicated CDSL parser.

Like `nsdl_parser`, this consumes structured `Block`/`Cell` data from
`pageobj.extract_blocks` and emits an `NSDLCASData` directly — no
detour through PROD's `process_cdsl_text`.

CDSL CAS layout (in document order; absolute page numbers vary by
holding count):

  - **Cover + account roster** — investor address block followed by the
    "Account Type / Account Details / ISINs / Value" summary table that
    enumerates each demat account and the MF-folios pseudo-account.
  - **Per-MF-folio descriptive blocks** — AMC Name, Scheme Name,
    Folio No, KYC, ISIN/UCC/RTA, one block group per folio. No
    balances here.
  - **Per-account transaction sections + holdings tables** — each
    section starts with `DP Name : <broker> ... BO ID :
    <dpid+clientid>`, followed by transactions, then a
    `HOLDING STATEMENT AS ON` header and the holdings table itself
    (9-10 cells per row).
  - **`MUTUAL FUND UNITS HELD AS ON` table** — one row per MF folio
    with full P&L (scheme name, ISIN, folio, ARN code, units, NAV,
    invested, current value, TER, commission, profit, return%).
  - **Notes / footer** at the end.

Some CDSL PDFs ship with a heavy Hindi-font overlay that makes
roughly half the table cells unreadable by content-stream extraction
(PROD fails on them too). The atom-level overlay filter in
`extract.py` recovers most of these; the remainder need a smarter
font-aware extractor and are out of scope here.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

from casparser.enums import FileType
from casparser.types import (
    DematAccount,
    DematOwner,
    Equity,
    MutualFund,
    NSDLCASData,
    StatementPeriod,
)

from . import pageobj
from ._investor import extract_nsdl_cdsl_investor
from .pageobj import Block

# --- patterns ---

ISIN_RE = re.compile(r"^[A-Z]{2}[0-9A-Z]{9}\d$")
INF_ISIN_RE = re.compile(r"^INF[0-9A-Z]{8}\d$")
INE_ISIN_RE = re.compile(r"^IN[E9][0-9A-Z]{8}\d$")
NUMERIC_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?$")

PERIOD_RE = re.compile(
    r"(?:for\s+the\s+period\s+from|statement\s+for\s+the\s+period\s+from)\s+"
    r"(\d{2}[-/][A-Za-z0-9]{2,3}[-/]\d{4})\s+to\s+(\d{2}[-/][A-Za-z0-9]{2,3}[-/]\d{4})",
    re.I,
)

DEMAT_TYPE_RE = re.compile(r"^(CDSL|NSDL)\s+Demat\s+Account\s*$", re.I)
PAN_RE = re.compile(r"(.+?)\s*\(\s*PAN\s*:\s*([^)]+?)\s*\)", re.I)

# Page-2 summary row carries `DP Id: <dp> Client Id : <client>`
# (whitespace around the colons varies).
SUMMARY_DPC_RE = re.compile(
    r"DP\s*Id\s*:\s*(\S+?)\s+Client\s*Id\s*:\s*(\d+)",
    re.I,
)
# Per-account holdings header: `DP Name : <broker>  DP ID : <dp>
# CLIENT ID : <client>`.
SECTION_DPC_RE = re.compile(
    r"DP\s*Name\s*:\s*(.+?)\s+DP\s*ID\s*:\s*(\S+)\s+CLIENT\s*ID\s*:\s*(\S+)",
    re.I | re.S,
)
# Transaction-page header: `DP Name : <broker>  BO ID : <16-char id>`.
# The id concatenates DP ID + Client ID, both 8 chars. CDSL DP IDs are
# numeric, NSDL DP IDs start with `IN`, so the 16-char field can be all
# digits (CDSL) or two letters + 14 digits (NSDL).
SECTION_BOID_RE = re.compile(
    r"DP\s*Name\s*:\s*(.+?)\s+(?:BO\s*ID|DPID)\s*:\s*([A-Z0-9]{16})",
    re.I | re.S,
)


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


# --- account key utilities ---


def _full_type(type_word: str) -> str:
    return f"{type_word.upper()} Demat Account"


def _account_key(type_word: str, dp_id: str, client_id: str) -> Tuple[str, str, str]:
    return (type_word.upper(), dp_id.strip(), client_id.strip())


def _split_bo_id(bo_id: str) -> Tuple[str, str, str]:
    """16-char BO ID → `(type_word, dp_id, client_id)`.

    NSDL DP IDs start with `IN` (8 chars total), CDSL DP IDs are pure
    digits. Both followed by an 8-digit Client ID."""
    if len(bo_id) != 16:
        return "", "", ""
    if bo_id[:2].upper() == "IN":
        return "NSDL", bo_id[:8], bo_id[8:]
    if bo_id.isdigit():
        return "CDSL", bo_id[:8], bo_id[8:]
    return "", "", ""


# --- parser entry point ---


def parse_cdsl(
    pdf_path: str,
    password: str,
    file_type: FileType = FileType.CDSL,
    *,
    _doc=None,
) -> NSDLCASData:
    atoms = pageobj.extract_atoms(pdf_path, password, _doc=_doc)
    blocks = pageobj.blocks_from_atoms(atoms)
    period = _find_period(blocks) or StatementPeriod(**{"from": "", "to": ""})

    # Phase 1: account roster from page 2 summary.
    accounts_by_key: Dict[Tuple[str, str, str], DematAccount] = {}
    ordered_accounts: List[DematAccount] = []
    mf_folios_account: Optional[DematAccount] = None
    pending_owners: List[DematOwner] = []

    for b in blocks:
        if b.page != 2:
            continue
        txt = b.text()
        ltxt = txt.lower()
        if "in the single name of" in ltxt or "in the joint name" in ltxt:
            pending_owners = []
            continue
        if PAN_RE.search(txt) and "Mutual" not in txt:
            for m in PAN_RE.finditer(txt):
                pending_owners.append(
                    DematOwner(
                        name=m.group(1).strip(),
                        PAN=m.group(2).strip(),
                    )
                )
            continue
        if _is_summary_demat_row(b):
            ac, key = _account_from_summary_row(b, pending_owners)
            if key not in accounts_by_key:
                accounts_by_key[key] = ac
                ordered_accounts.append(ac)
            continue
        if _is_summary_mf_folios_row(b):
            if mf_folios_account is None:
                mf_folios_account = _mf_folios_account_from_summary(b, pending_owners)
                ordered_accounts.append(mf_folios_account)
            continue

    # Phase 2: scheme-code → (ISIN, UCC, folio, name) map from the
    # descriptive MF blocks that follow the roster page. Their page
    # span depends on how many MF folios the investor holds; we
    # bound-scan from page 3 up to the start of the per-account
    # transaction section (detected via the `BO ID :` / `DPID :`
    # marker), and only consume blocks that match the descriptive
    # template (Scheme Name + Scheme Code + ISIN + UCC).
    scheme_meta: Dict[str, Dict[str, str]] = {}
    pending_scheme: Dict[str, str] = {}
    for b in blocks:
        if b.page < 3:
            continue
        if SECTION_BOID_RE.search(b.text()):
            break
        txt = b.text()
        if "Scheme Name :" in txt and "Scheme Code :" in txt:
            sm = re.search(
                r"Scheme\s*Name\s*:\s*(.+?)\s+Scheme\s*Code\s*:\s*(\S+)",
                txt,
                re.S,
            )
            if sm:
                pending_scheme = {
                    "scheme_name": sm.group(1).replace("\n", " ").strip(),
                    "scheme_code": sm.group(2).strip(),
                }
        elif "Folio No :" in txt:
            fm = re.search(r"Folio\s*No\s*:\s*(\S+)", txt)
            if fm:
                pending_scheme["folio"] = fm.group(1)
        elif "ISIN :" in txt and "UCC" in txt:
            im = re.search(r"ISIN\s*:\s*(\S+)", txt)
            um = re.search(r"UCC\s*:\s*([\w/]+)?", txt)
            if im and pending_scheme.get("scheme_code"):
                pending_scheme["isin"] = im.group(1)
                if um and um.group(1):
                    pending_scheme["ucc"] = um.group(1)
                scheme_meta[pending_scheme["scheme_code"]] = dict(pending_scheme)
                pending_scheme = {}

    # Phase 3: walk all post-cover pages for holdings tables.
    # We skip pages 1-2 (cover + roster) because the roster lines
    # contain CDSL/NSDL identifiers that would otherwise look like
    # section headers; the dispatch logic below handles the rest.
    cur_account: Optional[DematAccount] = None
    cur_mode: Optional[str] = None  # 'equities' | 'mf_holdings'

    for b in blocks:
        if b.page < 3:
            continue
        txt = b.text()
        ltxt = txt.lower()

        # Per-account section header — `DP Name : ... BO ID : ...`
        # or `DP Name : ... DPID : ...` (NSDL variant) form.
        m = SECTION_BOID_RE.search(txt)
        if m:
            broker = m.group(1).strip()
            bo_id = m.group(2)
            type_word, dp_id, client_id = _split_bo_id(bo_id)
            if type_word:
                ac_key = _account_key(type_word, dp_id, client_id)
                cur_account = accounts_by_key.get(ac_key)
                cur_mode = None
                continue

        # Or "DP Name : ... DP ID : ... CLIENT ID : ..."
        m = SECTION_DPC_RE.search(txt)
        if m:
            type_word = "CDSL"
            if "NSDL" in txt.upper() and "CDSL" not in txt.upper():
                type_word = "NSDL"
            broker, dp_id, client_id = m.groups()
            ac_key = _account_key(type_word, dp_id.strip(), client_id.strip())
            cur_account = accounts_by_key.get(ac_key)
            cur_mode = None
            continue

        # Transaction-statement section — switch OFF holdings mode so
        # transaction rows aren't parsed as equity rows.
        if "statement of transactions" in ltxt:
            cur_mode = None
            continue

        # Holdings section markers
        if "holding statement" in ltxt and "as on" in ltxt:
            cur_mode = "equities"
            continue
        if "mutual fund units held as on" in ltxt:
            cur_account = mf_folios_account
            cur_mode = "mf_holdings"
            continue

        # Skip column-header rows
        if _is_holdings_header(b) or _is_total_row(b):
            continue

        # Holdings rows
        if cur_account is None or cur_mode is None:
            continue
        if cur_mode == "equities":
            row = _parse_holdings_row(b)
            if row is None:
                pass
            else:
                isin, name, num_shares, price, value = row
                # CDSL Demat statements list ETFs (ISIN starting with
                # INF) in the same holdings table as equities, but
                # they're semantically mutual-fund units. Route them
                # to the mutual_funds list to mirror PROD's
                # classification.
                if INF_ISIN_RE.match(isin):
                    cur_account.mutual_funds.append(
                        MutualFund(
                            name=name,
                            isin=isin,
                            balance=num_shares,
                            nav=price,
                            value=value,
                        )
                    )
                else:
                    cur_account.equities.append(
                        Equity(
                            name=name,
                            isin=isin,
                            num_shares=num_shares,
                            price=price,
                            value=value,
                        )
                    )
        elif cur_mode == "mf_holdings":
            mf = _parse_mf_holdings_row(b, scheme_meta)
            if mf:
                cur_account.mutual_funds.append(mf)

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
    if len(block.cells) != 4:
        return False
    if not DEMAT_TYPE_RE.match(block.cells[0].text.strip()):
        return False
    return bool(SUMMARY_DPC_RE.search(block.cells[1].text))


def _is_summary_mf_folios_row(block: Block) -> bool:
    if len(block.cells) != 4:
        return False
    return bool(re.match(r"^Mutual\s+Fund\s+Folios", block.cells[0].text.strip(), re.I))


def _account_from_summary_row(
    block: Block, owners: List[DematOwner]
) -> Tuple[DematAccount, Tuple[str, str, str]]:
    type_word = DEMAT_TYPE_RE.match(block.cells[0].text.strip()).group(1).upper()
    broker_dp = block.cells[1].text
    lines = [ln.strip() for ln in broker_dp.split("\n") if ln.strip()]
    broker = lines[0] if lines else ""
    dpc = SUMMARY_DPC_RE.search(broker_dp)
    dp_id = dpc.group(1) if dpc else ""
    client_id = dpc.group(2) if dpc else ""
    folios = int(_to_decimal(block.cells[2].text))
    balance = _to_decimal(block.cells[3].text)
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
    )
    return ac, _account_key(type_word, dp_id, client_id)


def _mf_folios_account_from_summary(block: Block, owners: List[DematOwner]) -> DematAccount:
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
    )


# --- helpers ---


def _find_period(blocks: List[Block]) -> Optional[StatementPeriod]:
    for b in blocks:
        m = PERIOD_RE.search(b.text())
        if m:
            return StatementPeriod(**{"from": m.group(1), "to": m.group(2)})
    return None


def _is_holdings_header(block: Block) -> bool:
    """Block has no ISIN and looks like a column-label row."""
    txt = block.text()
    if re.search(r"\b(IN[EF9][0-9A-Z]{8}\d)\b", txt, re.I):
        return False
    ltxt = txt.lower().replace("\n", " ").replace("\t\t", " ")
    if "isin" in ltxt and ("security" in ltxt or "scheme name" in ltxt):
        return True
    if "current" in ltxt and "bal" in ltxt and "market" in ltxt:
        return True
    return False


def _is_total_row(block: Block) -> bool:
    first = block.cells[0].text.strip().lower() if block.cells else ""
    return first in ("sub total", "total", "grand total")


# --- equity holdings row ---


def _parse_holdings_row(block: Block) -> Optional[Tuple[str, str, Decimal, Decimal, Decimal]]:
    """CDSL holdings row → `(isin, name, num_shares, price, value)`.

    Column layout (post-`HOLDING STATEMENT`):
      ISIN | Security | Current Bal | Frozen Bal | Pledge Bal |
      Pledge Setup Bal | Free Bal | Market Price | Value (`)

    A few rows have a leading '@' marker cell between ISIN and name
    (suspended issue notation). Rows with all-`--` quantity cells
    (rights entitlements that haven't been exercised) are still
    parsed — `_to_decimal` maps `--` to 0. We use position-based
    assignment, not the last-three-numerics heuristic, because some
    rows have only 2 numeric cells (price + value) when all balance
    columns are `--`."""
    if not block.cells:
        return None
    first = block.cells[0].text.strip()
    if not ISIN_RE.match(first):
        return None
    isin = first

    # Find the data-cell boundary: the first cell after ISIN whose
    # text is a number or `--`. Everything between ISIN and that cell
    # is part of the security name (PDF renderer sometimes splits
    # multi-line security names across several cells with different
    # x-positions).
    data_start = None
    for i in range(1, len(block.cells)):
        t = block.cells[i].text.strip()
        if _looks_numeric(t) or t in ("--", "-"):
            data_start = i
            break
    if data_start is None or len(block.cells) - data_start < 3:
        return None
    name = (
        " ".join(
            c.text.replace("\n", " ").strip()
            for c in block.cells[1:data_start]
            if c.text.strip() and c.text.strip() not in ("@",)
        )
        or None
    )

    num_shares = _to_decimal(block.cells[data_start].text)
    price = _to_decimal(block.cells[-2].text)
    value = _to_decimal(block.cells[-1].text)
    return isin, name, num_shares, price, value


# --- MF holdings row (the `MUTUAL FUND UNITS HELD AS ON` section) ---


def _parse_mf_holdings_row(
    block: Block,
    scheme_meta: Dict[str, Dict[str, str]],
) -> Optional[MutualFund]:
    """MF holdings table row. Known templates:

    - **Full, with distribution-mode column (13 cells)**:
        name | ISIN | folio | ARN-or-DIRECT | units | NAV | invested
        | value | TER% | direct | commission | profit | return%

    - **Without distribution-mode column (7 cells)**:
        name | ISIN | folio | units | NAV | invested | value

    - **Reduced, with distribution-mode column (7 cells)**:
        name | ISIN | folio | ARN-or-DIRECT | units | NAV | value
        (no separate "invested / total cost" column)

    Discriminator: the cell two positions after the ISIN. In the
    distribution-mode layouts it carries an alphanumeric label
    (`ARN-####`, `DIRECT`, sometimes a folio-split fragment like
    `4/0`); otherwise it's the units value (a pure number).

    We then filter the cells after `data_start` to numeric tokens. The
    leading two are always units + NAV; the *current value* is the next
    column when it's the last one (reduced row) or the column after
    "invested" otherwise. A holdings statement always prints the
    current value, so when only three numerics survive we treat the
    third as the value (not the optional invested/cost column).
    """
    if len(block.cells) < 5:
        return None
    # Find the ISIN cell — usually cell 1.
    isin_idx = None
    for i in range(min(3, len(block.cells))):
        s = block.cells[i].text.strip()
        if ISIN_RE.match(s):
            isin_idx = i
            break
    if isin_idx is None:
        return None
    isin = block.cells[isin_idx].text.strip()

    # Name is whatever precedes the ISIN, joined.
    name = (
        " ".join(
            c.text.replace("\n", " ").strip() for c in block.cells[:isin_idx] if c.text.strip()
        )
        or None
    )

    # Folio = cell right after ISIN (`<digits>/<digits>` or just digits).
    folio = None
    if isin_idx + 1 < len(block.cells):
        folio = block.cells[isin_idx + 1].text.strip() or None

    # Discriminate 13-cell (has ARN/DIRECT) vs 7-cell (no such column).
    has_distrib_col = isin_idx + 2 < len(block.cells) and not _looks_numeric(
        block.cells[isin_idx + 2].text
    )
    data_start = isin_idx + (3 if has_distrib_col else 2)
    numerics = [c.text.strip() for c in block.cells[data_start:] if _looks_numeric(c.text)]
    if len(numerics) < 3:
        return None
    balance = _to_decimal(numerics[0])
    nav = _to_decimal(numerics[1])
    if len(numerics) >= 4:
        # units | NAV | invested | value | [TER, commission, profit, return]
        invested = _opt_decimal(numerics[2])
        value = _to_decimal(numerics[3])
    else:
        # Reduced row: units | NAV | value (no separate invested/cost).
        invested = None
        value = _to_decimal(numerics[2])
    pnl = _opt_decimal(numerics[-2]) if has_distrib_col and len(numerics) >= 6 else None
    ret = _opt_decimal(numerics[-1]) if has_distrib_col and len(numerics) >= 5 else None

    # Pull UCC from scheme_meta keyed on scheme_code (prefix of name)
    ucc = None
    if name:
        code_m = re.match(r"\s*([A-Z0-9]+)\s*-\s*", name)
        if code_m:
            meta = scheme_meta.get(code_m.group(1))
            if meta:
                ucc = meta.get("ucc")

    return MutualFund(
        name=name,
        isin=isin,
        balance=balance,
        nav=nav,
        value=value,
        total_cost=invested,
        ucc=ucc,
        folio=folio,
        pnl=pnl,
        **{"return": ret},
    )
