"""Regular expressions for parsing various sections in CAS."""

HEADER_RE = r"(?P<from>\d{2}-[a-zA-Z]{3}-\d{4})\s+to\s+(?P<to>\d{2}-[a-zA-Z]{3}-\d{4})"

FOLIO_RE = (
    r"Folio\s+No\s*:\s+([\d/\s]+)\s+.*?(?:PAN\s*:\s+([A-Z]{5}\d{4}[A-Z])\s+)?.*?"
    r"KYC\s*:\s*(OK|NOT\s+OK)\s*.*?(?:PAN\s*:\s*(OK|NOT\s+OK))?$"
)

SCHEME_RE = r"([\s\w]+)-\s*\d*\s*(.+?)\s*(?:\(Advisor\s*:\s*(.+?)\))*\s+Registrar\s*:\s*(.*)\s*$"
OPEN_UNITS_RE = r"Opening\s+Unit\s+Balance.+?([\d,.]+)"
CLOSE_UNITS_RE = r"Closing\s+Unit\s+Balance.+?([\d,.]+)"
VALUATION_RE = r"Valuation\s+on\s+(\d{2}-[A-Za-z]{3}-\d{4})\s*:\s*INR\s*([\d,.]+)"
NAV_RE = r"NAV\s+on\s+(\d{2}-[A-Za-z]{3}-\d{4})\s*:\s*INR\s*([\d,.]+)"

TRANSACTION_RE = (
    r"(\d{2}-[A-Za-z]{3}-\d{4})\t\t([^\t]+?)\t\t([(\d,.]+)\)*"
    r"(?:\t\t([(\d,.]+)\)*\t\t([(\d,.]+)\)*\t\t([(\d,.]+)\)*)*"
)
DIVIDEND_RE = r"dividend.+?(reinvest)*.+?@\s+Rs\.\s*([\d\.]+)\s+per\s+unit"

DESCRIPTION_TAIL_RE = r"\d{2}-[A-Za-z]{3}-\d{4}\t\t.*(\n[^\t]+)[\t|$]"
