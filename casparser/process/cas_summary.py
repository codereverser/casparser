import re
from decimal import Decimal

from dateutil import parser as date_parser

from casparser.enums import CASFileType
from casparser.exceptions import HeaderParseError
from casparser.types import (
    Folio,
    ProcessedCASData,
    Scheme,
    SchemeValuation,
    StatementPeriod,
)

from .regex import SCHEME_TAIL_RE, SUMMARY_DATE_RE, SUMMARY_ROW_RE
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
    statement_period = StatementPeriod(from_=hdr_data["date"], to=hdr_data["date"])

    folios = {}
    current_folio = None
    current_amc = "N/A"
    lines = text.split("\u2029")
    for line in lines:
        if len(folios) > 0 and re.search("Total", line, re.I):
            break
        scheme_tails = []
        if m := re.findall(SCHEME_TAIL_RE, line):
            for txt in m:
                line = line.replace(txt, "")
                scheme_tails.append(re.sub(r"\s+", " ", txt).strip())
        if m := re.search(SUMMARY_ROW_RE, line, re.DOTALL | re.MULTILINE | re.I):
            folio = m.group("folio").strip()
            if current_folio is None or current_folio != folio:
                current_folio = folio
                if folio not in folios:
                    folios[folio] = Folio(
                        folio=current_folio,
                        amc=current_amc,
                        PAN="N/A",
                        KYC="N/A",
                        PANKYC="N/A",
                        schemes=[],
                    )
            scheme = m.group("name")
            if len(scheme_tails) > 0:
                scheme = " ".join([scheme, *scheme_tails])
            scheme = re.sub(r"\(formerly.+?\)", "", scheme, flags=re.I | re.DOTALL).strip()
            rta = m.group("rta").strip()
            rta_code = m.group("code").strip()
            isin_ = m.group("isin")
            isin, amfi, scheme_type = isin_search(scheme, rta, rta_code, isin=isin_)
            scheme_data = Scheme(
                scheme=scheme,
                advisor="N/A",
                rta_code=rta_code,
                rta=rta,
                isin=isin,
                amfi=amfi,
                type=scheme_type or "N/A",
                open=Decimal(m.group("balance").replace(",", "_")),
                close=Decimal(m.group("balance").replace(",", "_")),
                close_calculated=Decimal(m.group("balance").replace(",", "_")),
                valuation=SchemeValuation(
                    date=date_parser.parse(m.group("date")).date(),
                    nav=Decimal(m.group("nav").replace(",", "_")),
                    value=Decimal(m.group("value").replace(",", "_")),
                ),
                transactions=[],
            )
            cost = m.group("cost")
            if cost is not None:
                scheme_data.valuation.cost = Decimal(cost.replace(",", "_"))
            folios[current_folio].schemes.append(scheme_data)
    return ProcessedCASData(
        cas_type=CASFileType.SUMMARY,
        statement_period=statement_period,
        folios=list(folios.values()),
    )
