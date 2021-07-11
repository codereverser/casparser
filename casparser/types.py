from datetime import date
from decimal import Decimal
from typing import Optional, List, Union
from typing_extensions import TypedDict


StatementPeriod = TypedDict("StatementPeriod", {"from": str, "to": str})


class InvestorInfoType(TypedDict):
    """Investor Info data structure."""

    name: str
    email: str
    address: str
    mobile: str


class TransactionDataType(TypedDict):
    """Mutual fund scheme transaction."""

    date: Union[date, str]
    description: str
    amount: Union[Decimal, float]
    units: Union[Decimal, float, None]
    nav: Union[Decimal, float, None]
    balance: Union[Decimal, float]
    type: str
    dividend_rate: Union[Decimal, float, None]


class SchemeValuationType(TypedDict):
    """Scheme valuation as of a given date."""

    date: Union[date, str]
    nav: Union[Decimal, float]
    value: Union[Decimal, float]


class SchemeType(TypedDict, total=False):
    """Mutual Fund Scheme data structure."""

    scheme_id: int
    scheme: str
    advisor: Optional[str]
    rta_code: str
    rta: str
    type: Optional[str]
    isin: Optional[str]
    amfi: Optional[str]
    open: Union[Decimal, float]
    close: Union[Decimal, float]
    close_calculated: Union[Decimal, float]
    valuation: SchemeValuationType
    transactions: List[TransactionDataType]


class FolioType(TypedDict, total=False):
    """Mutual Fund Folio data structure."""

    folio_id: int
    folio: str
    amc: str
    amc_id: int
    PAN: str
    KYC: str
    PANKYC: str
    schemes: List[SchemeType]


class CASParserDataType(TypedDict):
    """CAS Parser return data type."""

    statement_period: StatementPeriod
    folios: List[FolioType]
    investor_info: InvestorInfoType
    cas_type: str
    file_type: str
