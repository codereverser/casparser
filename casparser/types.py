from datetime import date
from decimal import Decimal
from typing import List, TypedDict, Union

from .enums import FileType

StatementPeriod = TypedDict("StatementPeriod", {"from": str, "to": str})


class InvestorInfoType(TypedDict):
    name: str
    email: str
    address: str
    mobile: str


class TransactionType(TypedDict):
    date: Union[date, str]
    description: str
    amount: Union[Decimal, float]
    units: Union[Decimal, float]
    nav: Union[Decimal, float]
    balance: Union[Decimal, float]


class SchemeType(TypedDict, total=False):
    scheme_id: int
    scheme: str
    advisor: str
    rta_code: str
    rta: str
    open: Union[Decimal, float]
    close: Union[Decimal, float]
    transactions: List[TransactionType]


class FolioType(TypedDict, total=False):
    folio_id: int
    folio: str
    amc: str
    amc_id: int
    PAN: str
    KYC: str
    PANKYC: str
    schemes: List[SchemeType]


class CASParserDataType(TypedDict):
    statement_period: StatementPeriod
    folios: List[FolioType]
    investor_info: InvestorInfoType
    file_type: FileType
