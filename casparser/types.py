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
    # For GIFT_IN / GIFT_OUT transfers: the *counterparty* folio named in
    # the description (the destination for a gift-out, the source for a
    # gift-in). Lets a donor's statement be linked to the donee's across
    # two CAS files. None for all other transaction types.
    gift_folio: Optional[str] = None


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
    # Non-fatal data-quality warnings raised during parsing. Currently
    # populated by the CAMS/KFin DETAILED parser, which reconciles each
    # scheme's transactions against the printed running Unit Balance
    # column (the statement's own checksum). A non-empty list means a
    # transaction row was likely dropped or mis-parsed — the parse still
    # returns, but the data for that scheme should not be trusted blindly.
    parse_warnings: List[str] = []
    model_config = ConfigDict(
        populate_by_name=True,
        use_enum_values=True,
    )


class DematOwner(BaseModel):
    name: str
    PAN: str


class Equity(BaseModel):
    name: Optional[str] = None
    isin: str
    num_shares: Decimal
    price: Decimal
    value: Decimal
    # Depository (NSDL/CDSL) statements identify an equity holding only by
    # ISIN. The exchange trading symbol (and exchange) are backfilled from the
    # ISIN database after parsing (see parsers._isin.batch_equity_symbols) so
    # downstream consumers can price the holding via a symbol-keyed feed.
    symbol: Optional[str] = None
    exchange: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def fix_float(cls, data: dict):
        for k, v in data.items():
            try:
                if issubclass(Decimal, cls.__annotations__[k]) and isinstance(v, str):
                    data[k] = v.replace(",", "_").replace("_", "")
            except TypeError:
                # Optional[...] / Union annotations (name / symbol / exchange)
                # aren't classes; only the required Decimal fields need the
                # comma-stripping treatment.
                pass
        return data


class Bond(BaseModel):
    """Corporate / government bond holding.

    Two source layouts feed this model — both are present in the same
    NSDL CAS, one per demat-account flavour:

    - **NSDL-account summary form** (8 data cells):
      `ISIN | name | frequency | coupon_rate | maturity | num_bonds |
      face_value | value`. All optional fields populated.
    - **CDSL-account detailed form** (13 data cells, identical layout to
      detailed equity rows): we only get `num_bonds`, `market_price`,
      `value`. `coupon_rate`/`frequency`/`maturity`/`face_value` are
      `None` here — the detailed table doesn't include them.
    """

    name: Optional[str] = None
    isin: str
    num_bonds: Decimal
    value: Decimal
    face_value: Optional[Decimal] = None
    coupon_rate: Optional[Decimal] = None
    coupon_frequency: Optional[str] = None
    maturity_date: Optional[str] = None
    market_price: Optional[Decimal] = None

    @model_validator(mode="before")
    @classmethod
    def fix_float(cls, data: dict):
        for k, v in data.items():
            try:
                if issubclass(Decimal, cls.__annotations__[k]) and isinstance(v, str):
                    data[k] = v.replace(",", "_").replace("_", "")
            except TypeError:
                # Optional[Decimal] / Union annotations land here; the
                # parser already strips commas before constructing the
                # model, so this is just a safety-net for required
                # Decimal fields.
                pass
        return data


class MutualFund(BaseModel):
    name: Optional[str] = None
    isin: str
    # Depository (NSDL/CDSL) statements identify an MF holding only by
    # ISIN — unlike RTA (CAMS/KFin) `Scheme` rows they carry no AMFI code
    # or scheme type. These are backfilled from the ISIN database after
    # parsing (see parsers._isin.batch_isin_metadata) so a demat holding
    # lines up with the same scheme from an RTA CAS on `amfi` / `type`.
    amfi: Optional[str] = None
    type: Optional[str] = None
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
        # Build a lookup from both attribute names and field aliases so that
        # aliased fields (e.g. ``return`` -> ``return_``) also get the
        # comma-stripping treatment.
        annotations_by_key: dict = {}
        for attr, annotation in cls.__annotations__.items():
            annotations_by_key[attr] = annotation
            field = cls.model_fields.get(attr)
            if field is not None and field.alias is not None:
                annotations_by_key[field.alias] = annotation
        for k, v in data.items():
            annotation = annotations_by_key.get(k)
            if annotation is not None and issubclass(Decimal, annotation) and isinstance(v, str):
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
    bonds: List[Bond] = []

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
    investor_info: InvestorInfo
    file_type: FileType
    model_config = ConfigDict(
        populate_by_name=True,
        use_enum_values=True,
    )
