from typing import Optional, Tuple

from casparser_isin import MFISINDb


def isin_search(
    scheme_name: str,
    rta: str,
    rta_code: str,
    isin: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Search isin db for ISIN and AMFI code.

    The underlying ``MFISINDb.isin_lookup`` does ISIN-first lookup
    internally when ``isin`` is supplied, but only after validating
    that ``rta`` is one of the known RTAs (CAMS / KARVY / FRANKLIN).
    The RTA check is deliberate -- if it trips, the parser captured a
    malformed RTA upstream and the right fix is to repair the parser,
    not to bypass the validation here.

    :param isin: Scheme ISIN code (from the scheme line, if present).
    :param scheme_name: Scheme name from CAS.
    :param rta: RTA for the scheme.
    :param rta_code: Scheme RTA code.
    """
    try:
        with MFISINDb() as db:
            scheme_data = db.isin_lookup(scheme_name, rta, rta_code, isin=isin)
            return scheme_data.isin, scheme_data.amfi_code, scheme_data.type
    except ValueError:
        return None, None, None
