"""Regular expressions for parsing various sections in CAS."""

date_re = r"(\d{2}-[A-Za-z]{3}-\d{4})"
amt_re = r"([(-]*\d[\d,.]+)\)*"

CAS_TYPE_RE = r"consolidated\s+account\s+(statement|summary)"
DETAILED_DATE_RE = r"(?P<from>\d{2}-[a-zA-Z]{3}-\d{4})\s+to\s+(?P<to>\d{2}-[a-zA-Z]{3}-\d{4})"
SUMMARY_DATE_RE = r"as\s+on\s+(?P<date>\d{2}-[a-zA-Z]{3}-\d{4})"
SUMMARY_ROW_RE = (
    r"([\d/\s]+)\s+([\s\w]+)-\s*\d*\s*(.+?)\s*([\d,.]+)\s*"
    r"(\d{2}-[A-Za-z]{3}-\d{4})\s*([\d,.]+)\s*([\d,.]+)\s*(\w+)\s*$"
)
SCHEME_TAIL_RE = r"(\n.+?)\t\t"

FOLIO_RE = (
    r"Folio\s+No\s*:\s+([\d/\s]+)\s*.*?(?:PAN\s*:\s*([A-Z]{5}\d{4}[A-Z])\s+)?.*?"
    r"(?:KYC\s*:\s*(OK|NOT\s+OK))?\s*.*?(?:PAN\s*:\s*(OK|NOT\s+OK))?$"
)

SCHEME_RE = r"([\s\w]+)-\s*\d*\s*(.+?)\s*(?:\(Advisor\s*:\s*(.+?)\))*\s+Registrar\s*:\s*(.*)\s*$"
REGISTRAR_RE = r"^\s*Registrar\s*:\s*(.*)\s*$"
OPEN_UNITS_RE = r"Opening\s+Unit\s+Balance.+?([\d,.]+)"
CLOSE_UNITS_RE = r"Closing\s+Unit\s+Balance.+?([\d,.]+)"
VALUATION_RE = r"Valuation\s+on\s+(\d{2}-[A-Za-z]{3}-\d{4})\s*:\s*INR\s*([\d,.]+)"
NAV_RE = r"NAV\s+on\s+(\d{2}-[A-Za-z]{3}-\d{4})\s*:\s*INR\s*([\d,.]+)"

TRANSACTION_RE1 = rf"{date_re}\t\t([^0-9].*)\t\t{amt_re}\t\t{amt_re}\t\t{amt_re}\t\t{amt_re}"
TRANSACTION_RE2 = rf"{date_re}\t\t([^0-9].*)\t\t{amt_re}(?:\t\t{amt_re}\t\t{amt_re}\t\t{amt_re})*"
DESCRIPTION_TAIL_RE = r"(\n.+?)(\t\t|$)"
DIVIDEND_RE = r"(?:dividend|idcw).+?(reinvest)*.*?@\s+Rs\.\s*([\d\.]+)\s+per\s+unit"
