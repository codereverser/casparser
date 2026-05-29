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

# Matches an IDCW / dividend transaction description. Captures the
# "reinvest" hint (if present) and the per-unit rupee value.
DIVIDEND_RE = re.compile(
    r"(?:div\.|dividend|idcw).+?(reinvest)*.*?@\s*Rs\.\s*([\d\.]+)(?:\s+per\s+unit)?",
    re.I | re.DOTALL,
)


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
        reinvest_flag, dividend_str = div_match.groups()
        dividend_rate = Decimal(dividend_str)
        txn_type = (
            TransactionType.DIVIDEND_REINVEST if reinvest_flag else TransactionType.DIVIDEND_PAYOUT
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
        if "switch" in description:
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
        if re.search(
            r"reversal|rejection|dishonoured|mismatch|insufficient\s+balance",
            description,
            re.I,
        ):
            txn_type = TransactionType.REVERSAL
        elif "switch" in description:
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
