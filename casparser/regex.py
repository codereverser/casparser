HEADER_RE = r'consolidated\s+account\s+statement\s+' \
            r'(?P<from>\d{2}-[a-zA-Z]{3}-\d{4})\s+to\s+' \
            r'(?P<to>\d{2}-[a-zA-Z]{3}-\d{4})\s+email\s+id:(?P<email>.+?)\s'

FOLIO_RE = r'Folio\s+No\s*:\s+(.+?)\s+PAN:\s+([A-Z]{5}\d{4}[A-Z])' \
           r'\s+KYC\s*:\s*(.+?)\s+PAN\s*:\s*(.+?)$'

SCHEME_RE = r'(\w+)-\s*\d*\s*(.+?)\s*\(Advisor\s*:\s*(.+?)\)\s+' \
            r'Registrar\s*:\s*(.*)\s*$'
OPEN_UNITS_RE = r'Opening\s+Unit\s+Balance.+?([\d,.]+)'
CLOSE_UNITS_RE = r'Closing\s+Unit\s+Balance.+?([\d,.]+)'
TRANSACTION_RE = r'(\d{2}-[A-Za-z]{3}-\d{4})\s*\t\t(.+?)\t\t([(\d,.]+)\)*' \
                 r'\t\t([(\d,.]+)\)*\t\t([(\d,.]+)\)*\t\t'
