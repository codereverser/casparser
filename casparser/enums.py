from enum import Enum, IntEnum, auto


class FileType(IntEnum):
    """Enum for CAS file source."""

    UNKNOWN = 0
    CAMS = 1
    KFINTECH = 2


class CASFileType(IntEnum):
    """Enum for CAS file type"""

    UNKNOWN = 0
    SUMMARY = 1
    DETAILED = 2


class TransactionType(Enum):
    PURCHASE = auto()
    PURCHASE_SIP = auto()
    REDEMPTION = auto()
    DIVIDEND_PAYOUT = auto()
    DIVIDEND_REINVEST = auto()
    SWITCH_IN = auto()
    SWITCH_IN_MERGER = auto()
    SWITCH_OUT = auto()
    SWITCH_OUT_MERGER = auto()
    TAX = auto()
    MISC = auto()
    UNKNOWN = auto()
