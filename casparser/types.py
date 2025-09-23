from datetime import date
from decimal import Decimal
from typing import List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    nominees: List[str] = []
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


class DematOwner(BaseModel):
    name: str
    PAN: str


class Equity(BaseModel):
    name: Optional[str] = None
    isin: str
    num_shares: Decimal
    price: Decimal
    value: Decimal

    @model_validator(mode="before")
    @classmethod
    def fix_float(cls, data: dict):
        for k, v in data.items():
            if issubclass(Decimal, cls.__annotations__[k]) and isinstance(v, str):
                data[k] = v.replace(",", "_").replace("_", "")
        return data


class MutualFund(BaseModel):
    name: Optional[str] = None
    isin: str
    balance: Decimal
    nav: Decimal
    value: Decimal
    avg_cost: Optional[Decimal] = None
    total_cost: Optional[Decimal] = None
    ucc: Optional[str] = None
    folio: Optional[str] = None
    pnl: Optional[Decimal] = None
    return_: Optional[Decimal] = Field(None, alias="return")

    @model_validator(mode="before")
    @classmethod
    def fix_float(cls, data: dict):
        for k, v in data.items():
            if (
                k in cls.__annotations__
                and issubclass(Decimal, cls.__annotations__[k])
                and isinstance(v, str)
            ):
                data[k] = v.replace(",", "_").replace("_", "")
        return data


class DematAccount(BaseModel):
    name: str
    type: str
    dp_id: Optional[str] = ""
    client_id: Optional[str] = ""
    folios: int
    balance: Decimal
    owners: List[DematOwner]
    equities: List[Equity]
    mutual_funds: List[MutualFund]

    @model_validator(mode="before")
    @classmethod
    def fix_float(cls, data: dict):
        for k, v in data.items():
            try:
                if issubclass(Decimal, cls.__annotations__[k]) and isinstance(v, str):
                    data[k] = v.replace(",", "_")
            except TypeError:
                pass
        return data


class NSDLCASData(BaseModel):
    accounts: List[DematAccount]
    statement_period: StatementPeriod
    investor_info: Optional[InvestorInfo] = None
    file_type: Optional[FileType] = None
    model_config = ConfigDict(
        populate_by_name=True,
        use_enum_values=True,
    )
