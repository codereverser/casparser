from decimal import Decimal
import re
from typing import Dict, Optional, Tuple

from dateutil import parser as date_parser

from ..enums import TransactionType, CASFileType
from ..exceptions import HeaderParseError, CASParseError
from .regex import DETAILED_DATE_RE, FOLIO_RE, SCHEME_RE, REGISTRAR_RE
from .regex import CLOSE_UNITS_RE, NAV_RE, OPEN_UNITS_RE, VALUATION_RE
from .regex import DIVIDEND_RE, TRANSACTION_RE1, TRANSACTION_RE2, DESCRIPTION_TAIL_RE
from ..types import FolioType, SchemeType
from .utils import isin_search


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
        reinvest_flag, dividend_str = div_match.groups()
        dividend_rate = Decimal(dividend_str)
        txn_type = (
            TransactionType.DIVIDEND_REINVEST if reinvest_flag else TransactionType.DIVIDEND_PAYOUT
        )
    elif units is None:
        if "stt" in description:
            txn_type = TransactionType.STT_TAX
        elif "stamp" in description:
            txn_type = TransactionType.STAMP_DUTY_TAX
        elif "segregat" in description:
            txn_type = TransactionType.SEGREGATION
        elif "tds" in description:
            txn_type = TransactionType.TDS_TAX
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
        if re.search("reversal|rejection|dishonoured", description, re.I):
            txn_type = TransactionType.REVERSAL
        elif "switch" in description:
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


def parse_transaction(line):
    for regex in (TRANSACTION_RE1, TRANSACTION_RE2):
        if m := re.search(regex, line, re.DOTALL | re.MULTILINE | re.I):
            return m


def process_detailed_text(text):
    """
    Process the text version of a CAS pdf and return the detailed summary.
    :param text:
    :return:
    """
    hdr_data = parse_header(text[:1000])
    statement_period = {"from": hdr_data["from"], "to": hdr_data["to"]}

    folios: Dict[str, FolioType] = {}
    current_folio = None
    current_amc = None
    curr_scheme_data = {}
    balance = Decimal(0.0)
    lines = text.split("\u2029")
    for idx, line in enumerate(lines):
        # Parse schemes with long names (single line) effectively pushing
        # "Registrar" column to the previous line
        if re.search(REGISTRAR_RE, line):
            line = "\t\t".join([lines[idx + 1], line])
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
                    "KYC": None if m.group(3) is None else m.group(3).strip(),
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
                rta = m.group(4).strip()
                rta_code = m.group(1).strip()
                isin, amfi, scheme_type = isin_search(scheme, rta, rta_code)
                curr_scheme_data: SchemeType = {
                    "scheme": scheme,
                    "advisor": advisor,
                    "rta_code": rta_code,
                    "type": scheme_type or "N/A",
                    "rta": rta,
                    "isin": isin,
                    "amfi": amfi,
                    "open": Decimal(0.0),
                    "close": Decimal(0.0),
                    "close_calculated": Decimal(0.0),
                    "valuation": {"date": None, "value": Decimal(0.0), "nav": Decimal(0.0)},
                    "transactions": [],
                }
        if not curr_scheme_data:
            continue
        if m := re.search(OPEN_UNITS_RE, line):
            curr_scheme_data["open"] = Decimal(m.group(1).replace(",", "_"))
            curr_scheme_data["close_calculated"] = curr_scheme_data["open"]
            balance = curr_scheme_data["open"]
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
        description_tail = ""
        if m := re.search(DESCRIPTION_TAIL_RE, line):
            description_tail = m.group(1).strip()
            line = line.replace(m.group(1), "")
        if m := parse_transaction(line):
            date = date_parser.parse(m.group(1)).date()
            desc = m.group(2).strip()
            if description_tail != "":
                desc = " ".join([desc, description_tail])
            amt = Decimal(m.group(3).replace(",", "_").replace("(", "-"))
            if m.group(4) is None:
                units = None
                nav = None
            else:
                units = Decimal(m.group(4).replace(",", "_").replace("(", "-"))
                nav = Decimal(m.group(5).replace(",", "_"))
                balance = Decimal(m.group(6).replace(",", "_").replace("(", "-"))
            txn_type, dividend_rate = get_transaction_type(desc, units)
            if txn_type == TransactionType.SEGREGATION:
                units = balance = amt
                amt = Decimal(0.0)
                nav = Decimal(0.0)
            if units is not None:
                curr_scheme_data["close_calculated"] += units
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
