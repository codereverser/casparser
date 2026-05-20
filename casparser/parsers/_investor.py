"""Investor-info extractors for the four supported CAS issuers.

Both extractors filter the source PDF's atoms to a top-left column on
a known page (page 1 for CAMS/KFin, page 2 for NSDL/CDSL), then walk
top-down picking out labelled fields. We use page-object atoms rather
than baseline-clustered lines so the right-column disclaimer text
that shares y-baselines with the investor block doesn't contaminate
the result.

The CAMS/KFin block carries the full quartet (name, email, address,
mobile). The NSDL/CDSL block carries only the name and address —
those CAS variants don't print the investor's email or mobile on the
statement, so those fields come back as empty strings.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, List, Optional

from casparser.exceptions import CASParseError
from casparser.types import InvestorInfo

from .pageobj import Atom, extract_atoms

if TYPE_CHECKING:  # pragma: no cover
    import pypdfium2 as pdfium


# Top-left column cutoffs. Everything to the right is the disclaimer
# paragraph (CAMS/KFin) or the cover-page banner (NSDL/CDSL). 200 is
# the conservative right edge that fits all observed templates.
_LEFT_COLUMN_X = 200.0


_EMAIL_RE = re.compile(r"Email\s*Id\s*:\s*(\S+@\S+)", re.I)
_MOBILE_RE = re.compile(r"Mobile\s*:\s*([+\d]+)", re.I)
_PHONE_RE = re.compile(r"^\s*Phone\s+Off\s*:", re.I)
_PINCODE_RE = re.compile(r"^\s*(?:Pin\s*code|PINCODE)\s*:\s*\d+", re.I)
_ID_MARKER_RE = re.compile(r"^\s*(?:CAS|NSDL)\s*ID\s*:", re.I)


def _left_column_atoms(atoms: List[Atom]) -> List[Atom]:
    """Filter to atoms in the top-left column, sorted top-down."""
    filtered = [a for a in atoms if a.x_left < _LEFT_COLUMN_X and a.text.strip()]
    filtered.sort(key=lambda a: -a.y_top)
    return filtered


def extract_cams_kfin_investor(
    pdf_path,
    password,
    *,
    _doc: "Optional[pdfium.PdfDocument]" = None,
    _atoms: Optional[List[List[Atom]]] = None,
) -> InvestorInfo:
    """Read the investor block from the top-left of page 1.

    Layout across CAMS and KFin templates:

      Email Id: <email>
      <Investor Name>
      <Address line 1>
      ...
      <Address line N>
      [Phone Off: ...]                  ← only on some KFin templates
      Mobile: <mobile>

    We anchor on `Email Id:` (always present on CAMS/KFin), then
    everything until `Mobile:` (exclusive) is name + address. The
    name is the first non-label line; the rest is address. Stray
    `Phone Off:` lines are dropped from the address.

    Every CAS statement carries this block by mandate. If we can't
    find it we raise `CASParseError` — a CAS without identifiable
    investor is malformed, not a "missing field" case.

    `_doc` / `_atoms`: dispatcher-provided overrides to avoid a
    second pypdfium2 open + page-object walk when the caller has
    already extracted atoms for the holdings parser.
    """
    pages = (
        _atoms
        if _atoms is not None
        else extract_atoms(
            pdf_path,
            password,
            _doc=_doc,
        )
    )
    block = _left_column_atoms(pages[0]) if pages else []

    email = ""
    mobile = ""
    name = ""
    address_lines: List[str] = []
    seen_email = False

    for atom in block:
        text = atom.text.strip()
        if m := _EMAIL_RE.match(text):
            email = m.group(1).strip()
            seen_email = True
            continue
        if m := _MOBILE_RE.match(text):
            mobile = m.group(1).strip()
            # Mobile is the last field of the investor block — stop here
            # so the transaction table that follows isn't picked up.
            break
        if not seen_email:
            continue
        if _PHONE_RE.match(text):
            continue
        if not name:
            name = text
        else:
            address_lines.append(text)

    if not name:
        raise CASParseError(
            "Could not extract investor info from CAMS/KFin CAS PDF. "
            "Expected an `Email Id:` line followed by name + address + "
            "`Mobile:` in the top-left column of page 1."
        )
    return InvestorInfo(
        name=name,
        email=email,
        address="\n".join(address_lines),
        mobile=mobile,
    )


def extract_nsdl_cdsl_investor(
    pdf_path,
    password,
    *,
    _doc: "Optional[pdfium.PdfDocument]" = None,
    _atoms: Optional[List[List[Atom]]] = None,
) -> InvestorInfo:
    """NSDL / CDSL print the investor block on page 2 (after the cover
    page). The block is delimited by a `CAS ID:` (CDSL) or `NSDL ID:`
    (NSDL) marker on top and a `PINCODE:` line on the bottom. Name is
    the first line after the marker; everything between is address.
    Email and mobile aren't printed in these CAS variants, so they
    come back as empty strings.

    Raises `CASParseError` if no investor block is found — a CAS
    without identifiable investor is malformed.

    `_doc` / `_atoms`: dispatcher-provided overrides; see
    `extract_cams_kfin_investor`.
    """
    pages = (
        _atoms
        if _atoms is not None
        else extract_atoms(
            pdf_path,
            password,
            _doc=_doc,
        )
    )
    block = _left_column_atoms(pages[1]) if len(pages) >= 2 else []

    name = ""
    address_lines: List[str] = []
    seen_marker = False
    for atom in block:
        text = atom.text.strip()
        if _ID_MARKER_RE.match(text):
            seen_marker = True
            continue
        if not seen_marker:
            continue
        if not name:
            name = text
            continue
        address_lines.append(text)
        if _PINCODE_RE.match(text):
            break

    if not name:
        raise CASParseError(
            "Could not extract investor info from NSDL/CDSL CAS PDF. "
            "Expected a `CAS ID:` / `NSDL ID:` marker followed by name + "
            "address in the top-left column of page 2."
        )
    return InvestorInfo(
        name=name,
        email="",
        address="\n".join(address_lines),
        mobile="",
    )
