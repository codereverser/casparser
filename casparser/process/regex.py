"""Regular expressions for parsing various sections in CAS."""

date_re = r"(\d{2}-[A-Za-z]{3}-\d{4})"
amt_re = r"([(-]*\d[\d,.]+)\)*"

isin_re = r"[A-Z]{2}[0-9A-Z]{9}[0-9]{1}"

CAS_TYPE_RE = r"consolidated\s+account\s+(statement|summary)"
DETAILED_DATE_RE = r"(?P<from>\d{2}-[a-zA-Z]{3}-\d{4})\s+to\s+(?P<to>\d{2}-[a-zA-Z]{3}-\d{4})"
SUMMARY_DATE_RE = r"as\s+on\s+(?P<date>\d{2}-[a-zA-Z]{3}-\d{4})"
SUMMARY_ROW_RE = (
    r"(?P<folio>[\d/\s]+?)(?P<isin>[A-Z]{2}[0-9A-Z]{9}[0-9]{1})?\s+(?P<code>[ \w]+)-"
    r"(?P<name>.+?)\s+(?P<cost>[\d,.]+)?\s+(?P<balance>[\d,.]+)\t\t"
    r"(?P<date>\d{2}-[A-Za-z]{3}-\d{4})\t\t(?P<nav>[\d,.]+)\t\t(?P<value>[\d,.]+)"
    r"\t\t(?P<rta>\w+)\s*$"
)
SCHEME_TAIL_RE = r"(\n.+?)\t\t"

AMC_RE = r"^(.+?\s+(MF|Mutual\s*Fund)|franklin\s+templeton\s+investments)$"
FOLIO_RE = r"^Folio\s+No\s*:\s+([\d/\s]+\d)\s"
FOLIO_KV_RE = r"(PAN|KYC)\s*:\s*([A-Z]{5}\d{4}[A-Z]|OK|NOT OK)"

NOMINEE_RE = r"\s*Nominee\s+[1-3]\s*:\s*(.*?)" * 3 + r"$"

SCHEME_RE = (
    r"(?P<code>[\s\w]+-*[gdp]?)-\s*\d*\s*(?P<name>.+?)(?:\t\t|\(\s*Advis|ISIN).*?"
    r"Registrar\s*:\s*(?P<rta>.*)\s*$"
)
SCHEME_KV_RE = r"""(\w+)\s*:\s*([-\w]+)"""

REGISTRAR_RE = r"^\s*Registrar\s*:\s*(.*)\s*$"
OPEN_UNITS_RE = r"Opening\s+Unit\s+Balance.+?([\d,.]+)"
CLOSE_UNITS_RE = r"Closing\s+Unit\s+Balance.+?([\d,.]+)"
COST_RE = r"Total\s+Cost\s+Value\s*:.+?[INR\s]*([\d,.]+)"
VALUATION_RE = (
    r"(?:Valuation|Market\s+Value)\s+on\s+(\d{2}-[A-Za-z]{3}-\d{4})\s*:\s*INR\s*([\d,.]+)"
)
NAV_RE = r"NAV\s+on\s+(\d{2}-[A-Za-z]{3}-\d{4})\s*:\s*INR\s*([\d,.]+)"

# Normal Transaction entries
TRANSACTION_RE1 = rf"{date_re}\t\t([^0-9].*)\t\t{amt_re}\t\t{amt_re}\t\t{amt_re}\t\t{amt_re}"
# Zero unit transactions (ref: #88)
TRANSACTION_RE2 = rf"{date_re}\t\t([^0-9].*)\t\t{amt_re}\t\t(?:{amt_re})*\t\t{amt_re}\t\t{amt_re}"
# Segregated portfolio entries
TRANSACTION_RE3 = rf"{date_re}\t\t([^0-9].*)\t\t{amt_re}\t\t{amt_re}(?:\t\t{amt_re}\t\t{amt_re})*"
# Tax transactions
TRANSACTION_RE4 = rf"{date_re}\t\t([^0-9].*)\t\t{amt_re}(?:\t\t{amt_re}\t\t{amt_re}\t\t{amt_re})*"
DESCRIPTION_TAIL_RE = r"(\n.+?)(\t\t|$)"
DIVIDEND_RE = r"(?:div\.|dividend|idcw).+?(reinvest)*.*?@\s*Rs\.\s*([\d\.]+)(?:\s+per\s+unit)?"
SCHEME_TAIL_RE = r"(\n.+?)(?:\t\t|$)"


DEMAT_STATEMENT_PERIOD_RE = (
    r"for\s+the\s+period\s+from\s+(?P<from>\d{2}-[a-zA-Z0-9]{2,3}-\d{4})"
    r"\s+to\s+(?P<to>\d{2}-[a-zA-Z0-9]{2,3}-\d{4})"
)
DEMAT_HEADER_RE = (
    r"((?:CDSL|NSDL)\s+demat\s+account)\s+(.+?)\s*DP\s*Id\s*:\s*(.+?)"
    r"\s*Client\s*Id\s*:\s*(\d+)\s+(\d+)\s+([\d,.]+)"
)
DEMAT_MF_HEADER_RE = r"Mutual Fund Folios\s+(\d+)\s+folios\s+(\d+)\s+([\d,.]+)"
DEMAT_AC_TYPE_RE = r"^(NSDL|CDSL)\s+demat\s+account|Mutual\s+Fund\s+Folios\s+\(F\)"
DEMAT_MF_TYPE_RE = r"^Mutual\s+Fund\s+Folios\s+\(F\)$"
DEMAT_AC_HOLDER_RE = r"([^\t\n]+?)\s*\(PAN\s*:\s*(.+?)\)"
DEMAT_DP_ID_RE = r"DP\s*Id\s*:\s*(.+?)\s*Client\s*Id\s*:\s*(\d+).+PAN"
NSDL_EQ_RE = (
    rf"^([A-Z]{{2}}[E|9][0-9A-Z]{{8}}[0-9]{{1}})"
    rf"\s*(.+?)\s*{amt_re}\s+([\d,.]+)\s+{amt_re}\s+{amt_re}$"
)
NSDL_MF_RE = rf"^(INF[0-9A-Z]{{8}}[0-9]{{1}})\s*(.*?)\s*{amt_re}\s+{amt_re}\s+{amt_re}$"
NSDL_CDSL_HOLDINGS_RE = (
    r"^([A-Z]{2}[0-9A-Z]{9}[0-9]{1})\s*(.+?)\s+" + rf"{amt_re}\s+" * 10 + rf"{amt_re}$"
)
NSDL_MF_HOLDINGS_RE = (
    rf"({isin_re})\n(.+?)[\n\t]+(.+?)\t\t(\w+?)\t\t{amt_re}"
    rf"\t\t{amt_re}\t\t{amt_re}\t\t{amt_re}\t\t{amt_re}\t\t{amt_re}(?:\t\t{amt_re})?$"
)
