from datetime import date
from decimal import Decimal
from typing import List, Optional, Union

from pydantic import BaseModel

from .enums import CASFileType, FileType, TransactionType


class StatementPeriod(BaseModel):
    from_: str
    to: str

    class Config:
        allow_population_by_field_name = True
        fields = {"from_": "from"}


class InvestorInfo(BaseModel):
    """Investor Info data structure."""

    name: str
    email: str
    address: str
    mobile: str


class TransactionData(BaseModel):
    """Mutual fund scheme transaction."""

    date: Union[date, str]
    description: str
    amount: Union[Decimal, float, None]
    units: Union[Decimal, float, None]
    nav: Union[Decimal, float, None]
    balance: Union[Decimal, float, None]
    type: TransactionType
    dividend_rate: Union[Decimal, float, None]


class SchemeValuation(BaseModel):
    """Scheme valuation as of a given date."""

    date: Union[date, str]
    nav: Union[Decimal, float]
    value: Union[Decimal, float]


class Scheme(BaseModel):
    """Mutual Fund Scheme data structure."""

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
    valuation: SchemeValuation
    transactions: List[TransactionData]


class Folio(BaseModel):
    """Mutual Fund Folio data structure."""

    folio: str
    amc: str
    PAN: Optional[str]
    KYC: Optional[str]
    PANKYC: Optional[str]
    schemes: List[Scheme]


class CASData(BaseModel):
    """CAS Parser return data type."""

    statement_period: StatementPeriod
    folios: List[Folio]
    investor_info: InvestorInfo
    cas_type: CASFileType
    file_type: FileType


class PartialCASData(BaseModel):
    """CAS Parser return data type."""

    investor_info: InvestorInfo
    file_type: FileType
    lines: List[str]


class ProcessedCASData(BaseModel):
    cas_type: CASFileType
    folios: List[Folio]
    statement_period: StatementPeriod
