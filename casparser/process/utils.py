from decimal import Decimal
from typing import Optional, Tuple

from casparser_isin import MFISINDb


def isin_search(
    scheme_name: str, rta: str, rta_code: str
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Search isin db for ISIN and AMFI code

    :param scheme_name: Scheme name from CAS
    :param rta: RTA for the scheme
    :param rta_code: Scheme RTA code
    """
    try:
        with MFISINDb() as db:
            scheme_data = db.isin_lookup(scheme_name, rta, rta_code)
            return scheme_data.isin, scheme_data.amfi_code, scheme_data.type
    except ValueError:
        return None, None, None
