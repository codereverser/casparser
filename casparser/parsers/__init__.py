"""Top-level dispatcher for `casparser.read_cas_pdf`.

v1.0 reorganisation: pdfminer.six and PyMuPDF are gone. Everything
runs on pypdfium2 with parsers that consume structured page-object
data directly (no text-rendering / regex round-trip for NSDL+CDSL,
column-aware layout reading for CAMS+KFin).

The four issuer-specific parsers live alongside this file:

  cams_detailed.py  → CAMS / KFin DETAILED statements
  cams_summary.py   → CAMS / KFin SUMMARY statements
  nsdl.py           → NSDL Consolidated Account Statement
  cdsl.py           → CDSL Consolidated Account Statement

`read_cas_pdf` sniffs the issuer + statement variant from the PDF's
first page, dispatches to the right parser, optionally sorts
transactions chronologically, and returns either `CASData` (CAMS/KFin)
or `NSDLCASData` (NSDL/CDSL).
"""

from __future__ import annotations

import io
import warnings
from typing import Union

from casparser.enums import CASFileType, FileType
from casparser.exceptions import CASParseError
from casparser.types import CASData, NSDLCASData

from .detect import _open_document, detect_cas_type, detect_file_type
from .utils import cas2csv, cas2json


def _sort_transactions(data: CASData) -> CASData:
    """For each scheme, sort transactions by date and re-compute the
    running balance from the opening balance."""
    for folio in data.folios:
        for idx, scheme in enumerate(folio.schemes):
            dates = [x.date for x in scheme.transactions]
            if dates == sorted(dates):
                continue
            sorted_txns = []
            balance = scheme.open
            for txn in sorted(scheme.transactions, key=lambda x: x.date):
                balance += txn.units or 0
                txn.balance = balance
                sorted_txns.append(txn)
            scheme.transactions = sorted_txns
            folio.schemes[idx] = scheme
    return data


def read_cas_pdf(
    filename: Union[str, io.IOBase],
    password: str,
    output: str = "dict",
    sort_transactions: bool = True,
    force_pdfminer: bool = False,
):
    """Parse a Consolidated Account Statement PDF.

    :param filename: path to the CAS PDF (or an open file-like object).
    :param password: PDF password (most CAS PDFs are encrypted with the
                     investor's PAN).
    :param output: `"dict"` (default) returns the typed model directly,
                   `"json"` returns its JSON serialisation, `"csv"`
                   returns a CSV string of transactions or holdings.
    :param sort_transactions: For CAMS / KFin DETAILED statements, sort
                              each scheme's transactions by date and
                              re-compute the running balance. Default
                              `True`.
    :param force_pdfminer: **Deprecated.** v1.0 dropped pdfminer in
                          favour of pypdfium2. Setting this to True
                          emits a `DeprecationWarning` and is otherwise
                          ignored.
    :return: `CASData` for CAMS/KFin issuers, `NSDLCASData` for
             NSDL/CDSL issuers, or a serialised form of either when
             `output` is `"json"` / `"csv"`.
    """
    if force_pdfminer:
        warnings.warn(
            "force_pdfminer is deprecated in casparser 1.0 — pdfminer "
            "is no longer a supported backend.",
            DeprecationWarning,
            stacklevel=2,
        )

    # Open the PDF exactly once and thread it through the detect /
    # parser / investor extractor calls — every pypdfium2 open re-runs
    # the password decrypt + content-stream parse, so the savings on
    # multi-page detailed statements are significant.
    doc = _open_document(filename, password)

    file_type = detect_file_type(filename, password, _doc=doc)
    if file_type == FileType.UNKNOWN:
        raise CASParseError(
            "Could not identify the CAS issuer. Supported issuers are "
            "CAMS, KFintech, NSDL, and CDSL."
        )

    if file_type in (FileType.CAMS, FileType.KFINTECH):
        cas_type = detect_cas_type(filename, password, _doc=doc)
        if cas_type == CASFileType.DETAILED:
            from . import cams_detailed

            data: Union[CASData, NSDLCASData] = cams_detailed.parse(
                filename,
                password,
                file_type=file_type,
                _doc=doc,
            )
        elif cas_type == CASFileType.SUMMARY:
            from . import cams_summary

            data = cams_summary.parse(
                filename,
                password,
                file_type=file_type,
                _doc=doc,
            )
        else:
            raise CASParseError(
                "Could not identify whether this is a DETAILED or " "SUMMARY CAMS / KFin statement."
            )
        if sort_transactions and isinstance(data, CASData):
            data = _sort_transactions(data)
    elif file_type == FileType.NSDL:
        from . import nsdl

        data = nsdl.parse_nsdl(
            filename,
            password,
            file_type=FileType.NSDL,
            _doc=doc,
        )
    elif file_type == FileType.CDSL:
        from . import cdsl

        data = cdsl.parse_cdsl(
            filename,
            password,
            file_type=FileType.CDSL,
            _doc=doc,
        )
    else:  # pragma: no cover — handled above
        raise CASParseError(f"Unsupported file type: {file_type}")

    if output == "dict":
        return data
    if output == "csv":
        return cas2csv(data)
    return cas2json(data)


__all__ = ["read_cas_pdf"]
