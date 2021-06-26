from decimal import Decimal
from typing import Optional

from casparser_isin import MFISINDb


def nav_search(isin: str) -> Optional[Decimal]:
    with MFISINDb() as db:
        return db.nav_lookup(isin)
