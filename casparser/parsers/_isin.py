from typing import Dict, Iterable, Optional, Tuple

from casparser_isin import ISINDb, MFISINDb


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


def batch_isin_metadata(
    isins: Iterable[str],
) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
    """Map each ISIN to ``(amfi_code, scheme_type)`` in a single DB session.

    Used to enrich demat (NSDL/CDSL) ``MutualFund`` holdings, which the
    depository statements identify only by ISIN, with the AMFI code and
    scheme type that RTA (CAMS/KFin) statements carry. Opening one
    ``MFISINDb`` session for the whole batch avoids the per-row connect
    overhead of calling :func:`isin_search` in a loop.

    Unknown or unresolvable ISINs map to ``(None, None)``.

    :param isins: ISINs to resolve (duplicates and falsy values ignored).
    """
    result: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    unique = {isin for isin in isins if isin}
    if not unique:
        return result
    with MFISINDb() as db:
        for isin in unique:
            try:
                rows = db.direct_isin_lookup(isin)
            except (ValueError, KeyError, TypeError):
                rows = None
            if rows:
                row = rows[0]
                result[isin] = (row.get("amfi_code"), row.get("type"))
            else:
                result[isin] = (None, None)
    return result


def batch_equity_symbols(
    isins: Iterable[str],
) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
    """Map each equity ISIN to ``(symbol, exchange)`` in a single DB session.

    Used to enrich demat (NSDL/CDSL) ``Equity`` holdings, which the depository
    statements identify only by ISIN, with the exchange trading symbol that
    price feeds key on. Opening one :class:`ISINDb` session for the whole batch
    avoids per-row connect overhead.

    Only ISINs the database resolves *and* that carry a symbol are returned;
    everything else (unknown ISIN, a bond / AIF with no listed symbol, or a
    database built before the symbol columns existed) is absent from the map.

    :param isins: ISINs to resolve (duplicates and falsy values ignored).
    """
    result: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    with ISINDb() as db:
        for isin, data in db.batch_isin_lookup(isins).items():
            if data.symbol:
                result[isin] = (data.symbol, data.exchange)
    return result
