from decimal import Decimal
import re
from typing import Optional, Tuple

from dateutil import parser as date_parser

from ..enums import TransactionType, CASFileType
from ..exceptions import HeaderParseError, CASParseError
from .regex import DETAILED_DATE_RE, FOLIO_RE, SCHEME_RE
from .regex import CLOSE_UNITS_RE, NAV_RE, OPEN_UNITS_RE, VALUATION_RE
from .regex import DESCRIPTION_TAIL_RE, DIVIDEND_RE, TRANSACTION_RE


def parse_header(text):
    """
    Parse CAS header data.
    :param text: CAS text
    """
    if m := re.search(DETAILED_DATE_RE, text, re.DOTALL | re.MULTILINE | re.I):
        return m.groupdict()
    raise HeaderParseError("Error parsing CAS header")


def get_transaction_type(
    description: str, units: Optional[Decimal]
) -> Tuple[TransactionType, Optional[Decimal]]:
    """Get transaction type from the description text."""

    dividend_rate = None
    description = description.lower()
    if div_match := re.search(DIVIDEND_RE, description, re.I | re.DOTALL):
        reinvest_flag, dividend_rate = div_match.groups()
        txn_type = (
            TransactionType.DIVIDEND_REINVEST if reinvest_flag else TransactionType.DIVIDEND_PAYOUT
        )
    elif units is None:
        if "stt" in description:
            txn_type = TransactionType.TAX
        else:
            txn_type = TransactionType.MISC
    elif units > 0:
        if "switch" in description:
            if "merger" in description:
                txn_type = TransactionType.SWITCH_IN_MERGER
            else:
                txn_type = TransactionType.SWITCH_IN
        elif "sip" in description or "systematic" in description:
            txn_type = TransactionType.PURCHASE_SIP
        else:
            txn_type = TransactionType.PURCHASE
    elif units < 0:
        if "switch" in description:
            if "merger" in description:
                txn_type = TransactionType.SWITCH_OUT_MERGER
            else:
                txn_type = TransactionType.SWITCH_OUT
        else:
            txn_type = TransactionType.REDEMPTION
    else:
        print(
            "Warning: Error identifying transaction. "
            "Please report the issue with the transaction description"
        )
        print(f"Txn description: {description} :: Units: {units}")
        txn_type = TransactionType.UNKNOWN

    return txn_type, dividend_rate


def process_detailed_text(text):
    """
    Process the text version of a CAS pdf and return the detailed summary.
    :param text:
    :return:
    """
    hdr_data = parse_header(text[:1000])
    statement_period = {"from": hdr_data["from"], "to": hdr_data["to"]}

    folios = {}
    current_folio = None
    current_amc = None
    curr_scheme_data = {}
    balance = Decimal(0.0)
    lines = text.split("\u2029")
    for line in lines:
        if m := re.search(DESCRIPTION_TAIL_RE, line, re.I | re.DOTALL):
            description_tail = m.group(1).rstrip()
            line = line.replace(description_tail, "")
        else:
            description_tail = ""
        if amc_match := re.search(r"^(.+?)\s+(MF|Mutual\s+Fund)$", line, re.I | re.DOTALL):
            current_amc = amc_match.group(0)
        elif m := re.search(FOLIO_RE, line, re.I | re.DOTALL):
            folio = m.group(1).strip()
            if current_folio is None or current_folio != folio:
                if curr_scheme_data and current_folio is not None:
                    folios[current_folio]["schemes"].append(curr_scheme_data)
                    curr_scheme_data = {}
                current_folio = folio
                folios[folio] = {
                    "folio": current_folio,
                    "amc": current_amc,
                    "PAN": (m.group(2) or "").strip(),
                    "KYC": m.group(3).strip(),
                    "PANKYC": None if m.group(4) is None else m.group(4).strip(),
                    "schemes": [],
                }
        elif m := re.search(SCHEME_RE, line, re.DOTALL | re.MULTILINE | re.I):
            if current_folio is None:
                raise CASParseError("Layout Error! Scheme found before folio entry.")
            scheme = re.sub(r"\(formerly.+?\)", "", m.group(2), flags=re.I | re.DOTALL).strip()
            if curr_scheme_data.get("scheme") != scheme:
                if curr_scheme_data:
                    folios[current_folio]["schemes"].append(curr_scheme_data)
                advisor = m.group(3)
                if advisor is not None:
                    advisor = advisor.strip()
                curr_scheme_data = {
                    "scheme": scheme,
                    "advisor": advisor,
                    "rta_code": m.group(1).strip(),
                    "rta": m.group(4).strip(),
                    "open": Decimal(0.0),
                    "close": Decimal(0.0),
                    "valuation": {"date": None, "value": 0, "nav": 0},
                    "transactions": [],
                }
                balance = Decimal(0.0)
        if not curr_scheme_data:
            continue
        if m := re.search(OPEN_UNITS_RE, line):
            curr_scheme_data["open"] = Decimal(m.group(1).replace(",", "_"))
            continue
        if m := re.search(CLOSE_UNITS_RE, line):
            curr_scheme_data["close"] = Decimal(m.group(1).replace(",", "_"))
        if m := re.search(VALUATION_RE, line, re.I):
            curr_scheme_data["valuation"].update(
                date=date_parser.parse(m.group(1)).date(),
                value=Decimal(m.group(2).replace(",", "_")),
            )
        if m := re.search(NAV_RE, line, re.I):
            curr_scheme_data["valuation"].update(
                date=date_parser.parse(m.group(1)).date(),
                nav=Decimal(m.group(2).replace(",", "_")),
            )
            continue
        if m := re.search(TRANSACTION_RE, line, re.DOTALL):
            date = date_parser.parse(m.group(1)).date()
            desc = m.group(2).strip() + description_tail
            amt = Decimal(m.group(3).replace(",", "_").replace("(", "-"))
            if m.group(4) is None:
                units = None
                nav = None
            else:
                units = Decimal(m.group(4).replace(",", "_").replace("(", "-"))
                nav = Decimal(m.group(5).replace(",", "_"))
                balance = Decimal(m.group(6).replace(",", "_"))
            txn_type, dividend_rate = get_transaction_type(desc, units)
            curr_scheme_data["transactions"].append(
                {
                    "date": date,
                    "description": desc,
                    "amount": amt,
                    "units": units,
                    "nav": nav,
                    "balance": balance,
                    "type": txn_type.name,
                    "dividend_rate": dividend_rate,
                }
            )
    if curr_scheme_data:
        folios[current_folio]["schemes"].append(curr_scheme_data)
    return {
        "cas_type": CASFileType.DETAILED.name,
        "statement_period": statement_period,
        "folios": list(folios.values()),
    }
