from datetime import date
from decimal import Decimal
from typing import List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from .enums import CASFileType, FileType, TransactionType


class StatementPeriod(BaseModel):
    from_: str = Field(alias="from")
    to: str
    model_config = ConfigDict(populate_by_name=True)


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
    amount: Union[Decimal, float, None] = None
    units: Union[Decimal, float, None] = None
    nav: Union[Decimal, float, None] = None
    balance: Union[Decimal, float, None] = None
    type: TransactionType
    dividend_rate: Union[Decimal, float, None] = None


class SchemeValuation(BaseModel):
    """Scheme valuation as of a given date."""

    date: Union[date, str]
    nav: Union[Decimal, float]
    cost: Union[Decimal, float, None] = None
    value: Union[Decimal, float]


class Scheme(BaseModel):
    """Mutual Fund Scheme data structure."""

    scheme: str
    advisor: Optional[str] = None
    rta_code: str
    rta: str
    type: Optional[str] = None
    isin: Optional[str] = None
    amfi: Optional[str] = None
    open: Union[Decimal, float]
    close: Union[Decimal, float]
    close_calculated: Union[Decimal, float]
    valuation: SchemeValuation
    transactions: List[TransactionData]


class Folio(BaseModel):
    """Mutual Fund Folio data structure."""

    folio: str
    amc: str
    PAN: Optional[str] = None
    KYC: Optional[str] = None
    PANKYC: Optional[str] = None
    schemes: List[Scheme]


class CASData(BaseModel):
    """CAS Parser return data type."""

    statement_period: StatementPeriod
    folios: List[Folio]
    investor_info: InvestorInfo
    cas_type: CASFileType
    file_type: FileType
    model_config = ConfigDict(
        populate_by_name=True,
        use_enum_values=True,
    )


class PartialCASData(BaseModel):
    """CAS Parser return data type."""

    investor_info: InvestorInfo
    file_type: FileType
    lines: List[str]


class ProcessedCASData(BaseModel):
    cas_type: CASFileType
    folios: List[Folio]
    statement_period: StatementPeriod
