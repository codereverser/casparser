from enum import Enum, auto


class AutoEnum(Enum):
    # noinspection PyMethodParameters,PyTypeChecker
    def _generate_next_value_(name, start, count, last_values) -> str:  # type: ignore
        """
        Uses the name as the automatic value, rather than an integer
        See https://docs.python.org/3/library/enum.html#using-automatic-values for reference
        """
        return name


class FileType(AutoEnum):
    """Enum for CAS file source."""

    UNKNOWN = auto()
    CAMS = auto()
    KFINTECH = auto()


class CASFileType(AutoEnum):
    """Enum for CAS file type"""

    UNKNOWN = auto()
    SUMMARY = auto()
    DETAILED = auto()


class FundType(AutoEnum):
    EQUITY = auto()
    DEBT = auto()
    UNKNOWN = auto()


class GainType(AutoEnum):
    STCG = auto()
    LTCG = auto()


class TransactionType(str, AutoEnum):
    PURCHASE = auto()
    PURCHASE_SIP = auto()
    REDEMPTION = auto()
    DIVIDEND_PAYOUT = auto()
    DIVIDEND_REINVEST = auto()
    SWITCH_IN = auto()
    SWITCH_IN_MERGER = auto()
    SWITCH_OUT = auto()
    SWITCH_OUT_MERGER = auto()
    STT_TAX = auto()
    STAMP_DUTY_TAX = auto()
    TDS_TAX = auto()
    SEGREGATION = auto()
    MISC = auto()
    UNKNOWN = auto()
    REVERSAL = auto()
