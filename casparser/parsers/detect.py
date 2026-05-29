"""Detect the CAS issuer and statement variant from a PDF.

We look at the first 1-2 pages of text for unambiguous source markers
(CAMS / KFin watermark, NSDL / CDSL header strings) and the
`Consolidated Account (Statement|Summary)` heading.

All public functions accept an optional pre-opened `pdfium.PdfDocument`
so the dispatcher can open the PDF exactly once per `read_cas_pdf`
call. When `_doc` is `None`, the function falls back to opening from
the path argument — keeping the path-based signature usable for
direct calls (unit tests, third-party consumers).
"""

from __future__ import annotations

import re
from typing import Optional

import pypdfium2 as pdfium
from pypdfium2._helpers.misc import PdfiumError

from casparser.enums import CASFileType, FileType
from casparser.exceptions import CASParseError, IncorrectPasswordError

_CAS_TYPE_RE = re.compile(
    r"consolidated\s+account\s+(statement|summary)",
    re.I,
)


def _open_document(pdf_path, password) -> pdfium.PdfDocument:
    """Open a PDF with pypdfium2, translating PdfiumError into the
    casparser exception hierarchy."""
    try:
        return pdfium.PdfDocument(pdf_path, password=password)
    except PdfiumError as e:
        msg = str(e)
        if "PASSWORD" in msg.upper() or "password" in msg:
            raise IncorrectPasswordError("Incorrect PDF password!") from e
        raise CASParseError(f"Unhandled error while opening PDF: {msg}") from e
    except TypeError as e:
        raise CASParseError(f"Invalid input: {e}") from e


def _read_text_sample(
    pdf_path,
    password,
    max_pages: int = 2,
    *,
    _doc: Optional[pdfium.PdfDocument] = None,
) -> str:
    """Extract text from the first `max_pages` of the PDF."""
    doc = _doc if _doc is not None else _open_document(pdf_path, password)
    out = []
    for page_num, page in enumerate(doc):
        if page_num >= max_pages:
            break
        tp = page.get_textpage()
        out.append(tp.get_text_bounded())
    return "\n".join(out)


def detect_file_type(
    pdf_path,
    password,
    *,
    _doc: Optional[pdfium.PdfDocument] = None,
) -> FileType:
    """Identify the issuer (CAMS / KFin / NSDL / CDSL) from the PDF
    text. Raises nothing — returns `FileType.UNKNOWN` on no match."""
    text = _read_text_sample(pdf_path, password, _doc=_doc)
    if "CAMSCASWS" in text:
        return FileType.CAMS
    if "KFINCASWS" in text:
        return FileType.KFINTECH
    if "NSDL Consolidated Account Statement" in text or "About NSDL" in text:
        return FileType.NSDL
    if "Central Depository Services (India) Limited" in text:
        return FileType.CDSL
    return FileType.UNKNOWN


def detect_cas_type(
    pdf_path,
    password,
    *,
    _doc: Optional[pdfium.PdfDocument] = None,
) -> CASFileType:
    """For CAMS / KFin only: SUMMARY vs DETAILED statement.
    NSDL / CDSL don't have this split."""
    text = _read_text_sample(pdf_path, password, max_pages=1, _doc=_doc)
    if m := _CAS_TYPE_RE.search(text):
        kind = m.group(1).lower().strip()
        if kind == "statement":
            return CASFileType.DETAILED
        if kind == "summary":
            return CASFileType.SUMMARY
    return CASFileType.UNKNOWN
