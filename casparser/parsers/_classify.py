"""Classification helpers shared across the CAMS / KFin parsers.

Two pure utilities:

- `get_transaction_type` maps a transaction description + signed units
  count to a `TransactionType` enum, also extracting the dividend rate
  for IDCW / dividend lines.
- `get_parsed_scheme_name` normalises a raw scheme name (drops
  `(formerly ...)`, `(erstwhile ...)`, `(Demat ...)` trailers, collapses
  whitespace).

These are pulled out of the old `casparser.process.cas_detailed` module
because the pypdfium2 DETAILED parser still needs them but the rest of
that module's text-rendering machinery is now gone.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional, Tuple

from casparser.enums import TransactionType

# Matches an IDCW / dividend transaction description and captures the
# per-unit rupee value. The "reinvest" hint is detected separately
# (REINVEST_RE) rather than as an inline group: with a lazy `.*?` on
# both sides of an optional group, the engine settles on the first
# complete match and never backtracks to populate the group, so it only
# captured "reinvest" when the word sat exactly where the minimal
# expansion stopped. "Reinvestment of IDCW @ Rs..." and "IDCW - Reinvest
# @ Rs..." both leaked through as PAYOUT. A plain substring search is
# unambiguous.
DIVIDEND_RE = re.compile(
    r"(?:div\.|dividend|idcw).*?@\s*Rs\.\s*([\d\.]+)(?:\s+per\s+unit)?",
    re.I | re.DOTALL,
)
REINVEST_RE = re.compile(r"reinvest", re.I)

# Systematic Transfer Plan. A switch by another name — money is moved
# between two schemes on a schedule. The two RTAs word it differently:
# CAMS prints "Systematic Transfer Plan Switch In/Out …" (already caught by
# the "switch" keyword), while KFintech prints it letter-spaced as
# "S T P In/Out (…)", which matches none of the plain substring checks and
# used to fall through to PURCHASE / REDEMPTION. Detecting it here — spacing
# tolerated — lets both RTAs classify consistently as SWITCH_IN / SWITCH_OUT.
STP_RE = re.compile(r"\bs\s*t\s*p\b|systematic\s+transfer", re.I)

# Counterparty folio embedded in a gift transfer description. Both RTAs
# name the other folio but punctuate differently — KFin uses a colon
# ("Folio No: 12345678901"), CAMS a dot ("Folio No.87654321") — so accept
# either separator. Used to link a donor's GIFT_OUT to the donee's GIFT_IN
# across two CAS files.
GIFT_FOLIO_RE = re.compile(r"Folio\s+No\s*[:.]\s*(\d+)", re.I)


def extract_gift_folio(description: str) -> Optional[str]:
    """Return the counterparty folio number named in a gift description,
    or None if absent."""
    if m := GIFT_FOLIO_RE.search(description or ""):
        return m.group(1)
    return None


def get_transaction_type(
    description: str, units: Optional[Decimal]
) -> Tuple[TransactionType, Optional[Decimal]]:
    """Classify a transaction by its description + units sign.

    Returns `(transaction_type, dividend_rate_or_None)`. The dividend
    rate is only set for IDCW / dividend transactions.
    """
    dividend_rate: Optional[Decimal] = None
    description = description.lower()
    if div_match := DIVIDEND_RE.search(description):
        dividend_rate = Decimal(div_match.group(1))
        txn_type = (
            TransactionType.DIVIDEND_REINVEST
            if REINVEST_RE.search(description)
            else TransactionType.DIVIDEND_PAYOUT
        )
    elif units is None:
        if "stt" in description:
            txn_type = TransactionType.STT_TAX
        elif "stamp" in description:
            txn_type = TransactionType.STAMP_DUTY_TAX
        elif "tds" in description:
            txn_type = TransactionType.TDS_TAX
        else:
            txn_type = TransactionType.MISC
    elif units > 0:
        if "gift" in description:
            txn_type = TransactionType.GIFT_IN
        elif "switch" in description or STP_RE.search(description):
            txn_type = (
                TransactionType.SWITCH_IN_MERGER
                if "merger" in description
                else TransactionType.SWITCH_IN
            )
        elif "segregat" in description:
            txn_type = TransactionType.SEGREGATION
        elif (
            "sip" in description
            or "systematic" in description
            or re.search(r"instal+ment", description, re.I)
            or re.search(r"sys.+?invest", description, re.I | re.DOTALL)
        ):
            txn_type = TransactionType.PURCHASE_SIP
        else:
            txn_type = TransactionType.PURCHASE
    elif units < 0:
        if "gift" in description:
            txn_type = TransactionType.GIFT_OUT
        elif re.search(
            r"reversal|rejection|dishonoured|mismatch|insufficient\s+balance|"
            r"payment\s+not\s+received",
            description,
            re.I,
        ):
            txn_type = TransactionType.REVERSAL
        elif "switch" in description or STP_RE.search(description):
            txn_type = (
                TransactionType.SWITCH_OUT_MERGER
                if "merger" in description
                else TransactionType.SWITCH_OUT
            )
        else:
            txn_type = TransactionType.REDEMPTION
    else:
        txn_type = TransactionType.UNKNOWN

    return txn_type, dividend_rate


def get_parsed_scheme_name(scheme: str) -> str:
    """Strip `(formerly ...)`, `(erstwhile ...)`, `(Demat ...)`,
    `(Non-Demat ...)` trailers; collapse whitespace; trim trailing
    punctuation."""
    scheme = re.sub(
        r"\((formerly|erstwhile).+?\)",
        "",
        scheme,
        flags=re.I | re.DOTALL,
    ).strip()
    scheme = re.sub(
        r"\((Demat|Non-Demat).*",
        "",
        scheme,
        flags=re.I | re.DOTALL,
    ).strip()
    scheme = re.sub(r"\s+", " ", scheme).strip()
    return re.sub(r"[^a-zA-Z0-9_)]+$", "", scheme).strip()
