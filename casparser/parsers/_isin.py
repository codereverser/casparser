from typing import Optional, Tuple

from casparser_isin import MFISINDb


def isin_search(
    scheme_name: str,
    rta: str,
    rta_code: str,
    isin: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Look up `(ISIN, AMFI, type)` for a CAS scheme.

    The primary path matches on `(scheme_name, rta, rta_code)`. When
    that returns no hit but the caller passed an `isin` (e.g., parsed
    inline from the scheme header), fall back to a direct ISIN lookup.
    The fallback bypasses RTA mis-detection that can happen when the
    `Registrar:` value gets mangled by multi-line rendering on
    pypdfium2's char extraction.

    :param scheme_name: Normalised scheme name from the CAS.
    :param rta: Registrar (`CAMS` / `KFINTECH` / `FTAMIL` …).
    :param rta_code: Scheme's per-RTA code.
    :param isin: Optional ISIN hint pulled from the scheme header.
    """
    with MFISINDb() as db:
        try:
            scheme_data = db.isin_lookup(scheme_name, rta, rta_code, isin=isin)
            return scheme_data.isin, scheme_data.amfi_code, scheme_data.type
        except ValueError:
            pass
        if isin:
            try:
                rows = db.direct_isin_lookup(isin)
                if rows:
                    row = rows[0]
                    return row["isin"], row["amfi_code"], row["type"]
            except (ValueError, KeyError, TypeError):
                pass
    return None, None, None
