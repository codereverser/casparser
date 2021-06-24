from decimal import Decimal
import re

from dateutil import parser as date_parser

from ..enums import CASFileType
from ..exceptions import HeaderParseError
from .regex import SUMMARY_DATE_RE, SUMMARY_ROW_RE
from .utils import isin_search


def parse_header(text):
    """
    Parse CAS header data.
    :param text: CAS text
    """
    if m := re.search(SUMMARY_DATE_RE, text, re.DOTALL | re.MULTILINE | re.I):
        return m.groupdict()
    raise HeaderParseError("Error parsing CAS header")


def process_summary_text(text):
    """
    Process the text version of a CAS pdf and return the detailed summary.
    :param text:
    :return:
    """
    hdr_data = parse_header(text[:1000])
    statement_period = {"from": hdr_data["date"], "to": hdr_data["date"]}

    folios = {}
    current_folio = None
    current_amc = "N/A"
    curr_scheme_data = {}
    lines = text.split("\u2029")
    for line in lines:
        if len(folios) > 0 and re.search("Total", line, re.I):
            break
        if m := re.search(SUMMARY_ROW_RE, line, re.DOTALL | re.MULTILINE | re.I):
            folio = m.group(1).strip()
            if current_folio is None or current_folio != folio:
                current_folio = folio
                folios[folio] = {
                    "folio": current_folio,
                    "amc": current_amc,
                    "PAN": "N/A",
                    "KYC": None,
                    "PANKYC": None,
                    "schemes": [],
                }
            scheme = re.sub(r"\(formerly.+?\)", "", m.group(3), flags=re.I | re.DOTALL).strip()
            if curr_scheme_data.get("scheme") != scheme:
                if curr_scheme_data:
                    folios[current_folio]["schemes"].append(curr_scheme_data)
                rta = m.group(8).strip()
                rta_code = m.group(2).strip()
                isin, amfi, scheme_type = isin_search(scheme, rta, rta_code)
                curr_scheme_data = {
                    "scheme": scheme,
                    "advisor": "N/A",
                    "rta_code": rta_code,
                    "rta": rta,
                    "isin": isin,
                    "amfi": amfi,
                    "type": scheme_type or "N/A",
                    "open": Decimal(m.group(4).replace(",", "_")),
                    "close": Decimal(m.group(4).replace(",", "_")),
                    "valuation": {
                        "date": date_parser.parse(m.group(5)).date(),
                        "nav": Decimal(m.group(6).replace(",", "_")),
                        "value": Decimal(m.group(7).replace(",", "_")),
                    },
                    "transactions": [],
                }
    if curr_scheme_data:
        folios[current_folio]["schemes"].append(curr_scheme_data)
    return {
        "cas_type": CASFileType.SUMMARY.name,
        "statement_period": statement_period,
        "folios": list(folios.values()),
    }
