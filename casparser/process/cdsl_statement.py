import re
from decimal import Decimal

from casparser.exceptions import HeaderParseError
from casparser.types import (
    DematAccount,
    DematOwner,
    Equity,
    MutualFund,
    NSDLCASData,
    StatementPeriod,
)

from .regex import DEMAT_STATEMENT_PERIOD_RE

CDSL_DP_ID_RE = (
    r"DP\s*Name\s*:\s*(.+?)\s*DP\s*ID\s*:\s*(\d+)\s*CLIENT\s*ID\s*:\s*(\d+)"
)

CDSL_DP_NAME_BOID_RE = (
    r"DP\s*Name\s*:\s*(.+?)\s*BO\s*ID\s*:\s*(\d+)"
)

DEMAT_HOLDINGS_HEADER_RE = r"HOLDING\s+STATEMENT"

ISIN_LINE_RE = r"^(?P<isin>[A-Z]{2}[0-9A-Z]{9}\d)"

_PAGE_SKIP_RE = re.compile(
    r"^(?:Page\s+\d+\s+of\s+\d+|Central\s+Depository|CONSOLIDATED\s+ACCOUNT"
    r"|FORM\s+AND\s+INVESTMENTS|Investments|A\s+Wing|Lower\s+Parel)",
    re.I,
)

_PAGE_HEADER_SKIP_RE = re.compile(
    r"^(?:RATAN\s+KUMAR|(?:ISIN|Security)\t|Account\s+Type|"
    r"TER\s+&\s+COMMISSION|YOUR\s+CONSOLIDATED|TER\s+&|Portfolio\s+Value)",
    re.I,
)


def parse_decimal(value):
    if isinstance(value, str):
        value = value.replace(",", "")
        if value in ("--", "", "0", "N.A"):
            return Decimal("0")
        return Decimal(value)
    return Decimal(str(value)) if value is not None else Decimal("0")


def parse_header(text):
    if m := re.search(
        DEMAT_STATEMENT_PERIOD_RE,
        text,
        re.DOTALL | re.MULTILINE | re.I,
    ):
        return m.groupdict()
    raise HeaderParseError("Error parsing CAS header")


def _is_numeric_token(token):
    return bool(re.match(r"^[\d,]+\.?\d*$|^--$", token))


def _split_name_numeric(parts):
    name_parts = []
    numeric_parts = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if _is_numeric_token(p):
            numeric_parts.append(p)
        else:
            if not re.match(ISIN_LINE_RE, p):
                name_parts.append(p)
    return name_parts, numeric_parts


def process_cdsl_text(text):
    hdr_data = parse_header(text)
    statement_period = StatementPeriod(from_=hdr_data["from"], to=hdr_data["to"])

    lines = text.split("\u2029")

    dp_info_map = {}
    for line in lines:
        if m := re.search(CDSL_DP_ID_RE, line, re.I):
            dp_name, dp_id, client_id = m.groups()
            dp_name_clean = dp_name.strip()
            dp_info_map[dp_name_clean] = {
                "name": dp_name_clean,
                "dp_id": dp_id,
                "client_id": client_id,
            }

    demat_accounts = {}
    for dp_name, info in dp_info_map.items():
        key = (info["dp_id"], info["client_id"])
        demat_accounts[key] = {
            "name": dp_name,
            "type": "CDSL Demat Account",
            "dp_id": info["dp_id"],
            "client_id": info["client_id"],
            "folios": 0,
            "balance": Decimal("0"),
            "owners": [],
            "equities": [],
            "mutual_funds": [],
        }

    dp_name_to_key = {}
    for key, acct in demat_accounts.items():
        dp_name_to_key[acct["name"].upper()] = key

    current_account_key = None
    in_holdings = False
    in_mf_holdings = False
    mf_scheme_name_lines = []
    mf_folio_holdings = []

    for idx, line in enumerate(lines):
        line = line.replace("\u00ad", "")
        stripped = line.strip()

        if not stripped:
            continue

        if _PAGE_SKIP_RE.match(stripped):
            continue

        if re.search(r"MUTUAL\s+FUND\s+UNITS\s+HELD\s+WITH\s+MF", stripped, re.I):
            in_mf_holdings = True
            in_holdings = False
            mf_scheme_name_lines = []
            continue

        if in_mf_holdings:
            if re.search(r"MUTUAL\s+FUND\s+UNITS\s+HELD\s+AS\s+ON",
                         stripped, re.I):
                mf_scheme_name_lines = []
                continue
            if re.search(r"Grand\s+Total|Average\s+Total\s+Expense|Load\s+Structures|"
                         r"Statement\s+for\s+the\s+period|Notes|About\s+CDSL",
                         stripped, re.I):
                in_mf_holdings = False
                continue
            if _PAGE_HEADER_SKIP_RE.match(stripped):
                continue
            if re.search(r"Scheme\s+Name|^ISIN\b|Date\s+Transaction|Opening\s+Balance|"
                         r"Closing\s+Balance|Transaction\s+Description|Folio\s+No",
                         stripped, re.I):
                continue
            if re.search(r"(?:AMC|Mutual\s+Fund)\s*$", stripped, re.I) and not re.match(
                ISIN_LINE_RE, line
            ):
                continue

            has_isin = re.search(r"INF[A-Z0-9]{8}\d", re.sub(r"\s+", "", line))
            if not has_isin:
                continue

            parts = line.split("\t\t")
            stripped_parts = [p.strip() for p in parts if p.strip()]

            isin = ""
            scheme_name_parts = []
            folio = ""
            numeric_fields = []
            found_isin = False

            for index, p in enumerate(stripped_parts):
                if re.match(r"^INF[A-Z0-9]{8}\d$", p):
                    isin = p
                    found_isin = True
                    continue
                elif re.match(r"^INF", p):
                    if re.match(r"^INF[A-Z0-9]{8}\d$", p + stripped_parts[index + 1]):
                        isin = p + stripped_parts[index + 1]
                        found_isin = True
                        continue
                if not found_isin:
                    scheme_name_parts.append(p)
                    continue
                if not folio and p not in ("DIRECT", "") and not p.startswith("ARN"):
                    folio = p
                    continue
                if _is_numeric_token(p):
                    numeric_fields.append(p)

            scheme_name = " ".join(scheme_name_parts)
            scheme_name = re.sub(r"\s+", " ", scheme_name).strip()

            closing_bal = Decimal("0")
            nav = Decimal("0")
            valuation = Decimal("0")

            if len(numeric_fields) >= 4:
                closing_bal = parse_decimal(numeric_fields[0])
                nav = parse_decimal(numeric_fields[1])
                valuation = parse_decimal(numeric_fields[3])

            mf_folio_holdings.append({
                "isin": isin,
                "name": scheme_name,
                "balance": str(closing_bal),
                "nav": str(nav),
                "value": str(valuation),
            })

            continue

        if re.search(CDSL_DP_NAME_BOID_RE, line, re.I):
            in_holdings = False
            m = re.search(CDSL_DP_NAME_BOID_RE, line, re.I)
            dp_name = m.group(1).strip().upper()
            current_account_key = dp_name_to_key.get(dp_name)
            continue

        if re.search(DEMAT_HOLDINGS_HEADER_RE, stripped, re.I):
            in_holdings = True
            continue

        if not in_holdings or current_account_key is None:
            continue

        if re.search(r"Portfolio\s+Value\s+`", stripped, re.I):
            in_holdings = False
            continue

        if _PAGE_HEADER_SKIP_RE.match(stripped):
            continue

        if m := re.search(ISIN_LINE_RE, line, re.MULTILINE):
            isin = m.group("isin")
            is_mf = isin.startswith("INF")
            parts = re.split(r"[\t\n]+", line)
            name_parts, numeric_parts = _split_name_numeric(parts)
            name = re.sub(r"\s+", " ", " ".join(name_parts)).strip()

            if len(numeric_parts) >= 2:
                price = parse_decimal(numeric_parts[-2]) if len(numeric_parts) >= 3 else Decimal("0")
                value = parse_decimal(numeric_parts[-1])
                if price != 0:
                    balance = round(value / price, 3)
                else:
                    balance = parse_decimal(numeric_parts[-3]) if len(numeric_parts) >= 3 else Decimal("0")
            else:
                continue

            if is_mf:
                demat_accounts[current_account_key]["mutual_funds"].append({
                    "isin": isin,
                    "name": name,
                    "balance": str(balance),
                    "nav": str(price),
                    "value": str(value),
                })
            else:
                demat_accounts[current_account_key]["equities"].append({
                    "isin": isin,
                    "name": name,
                    "num_shares": str(balance),
                    "price": str(price),
                    "value": str(value),
                })

    account_objects = []
    for key, acct_data in demat_accounts.items():
        equities = [Equity(**e) for e in acct_data["equities"]]
        mutual_funds = [MutualFund(**m) for m in acct_data["mutual_funds"]]

        total_value = sum(
            (Decimal(e["value"]) for e in acct_data["equities"])
        ) + sum(
            (Decimal(m["value"]) for m in acct_data["mutual_funds"])
        )

        account_objects.append(DematAccount(
            name=acct_data["name"],
            type=acct_data["type"],
            dp_id=acct_data["dp_id"],
            client_id=acct_data["client_id"],
            folios=len(equities) + len(mutual_funds),
            balance=total_value,
            owners=[],
            equities=equities,
            mutual_funds=mutual_funds,
        ))

    if mf_folio_holdings:
        mf_list = [MutualFund(**m) for m in mf_folio_holdings]
        mf_total = sum(Decimal(m["value"]) for m in mf_folio_holdings)
        account_objects.append(DematAccount(
            name="Mutual Fund Folios",
            type="MF",
            dp_id="",
            client_id="",
            folios=len(mf_folio_holdings),
            balance=mf_total,
            owners=[],
            equities=[],
            mutual_funds=mf_list,
        ))

    return NSDLCASData(
        statement_period=statement_period,
        accounts=account_objects,
    )
