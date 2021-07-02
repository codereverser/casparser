from collections import UserDict
from datetime import date
from decimal import Decimal
import re
from typing import Optional

from casparser_isin import MFISINDb

CII_DATA = {
    "FY2001-02": 100,
    "FY2002-03": 105,
    "FY2003-04": 109,
    "FY2004-05": 113,
    "FY2005-06": 117,
    "FY2006-07": 122,
    "FY2007-08": 129,
    "FY2008-09": 137,
    "FY2009-10": 148,
    "FY2010-11": 167,
    "FY2011-12": 184,
    "FY2012-13": 200,
    "FY2013-14": 220,
    "FY2014-15": 240,
    "FY2015-16": 254,
    "FY2016-17": 264,
    "FY2017-18": 272,
    "FY2018-19": 280,
    "FY2019-20": 289,
    "FY2020-21": 301,
}


class _CII(UserDict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.years = list(sorted(self.data.keys()))
        self._min_year = self.years[0]
        self._max_year = self.years[-1]

    def __missing__(self, key):
        if not re.search(r"FY\d{4}-\d{2}", key):
            raise ValueError("Invalid FY year format.")
        elif key <= self._min_year:
            return self.data[self._min_year]
        elif key >= self._max_year:
            return self.data[self._max_year]
        raise KeyError(key)


CII = _CII(CII_DATA)


def nav_search(isin: str) -> Optional[Decimal]:
    with MFISINDb() as db:
        return db.nav_lookup(isin)


def get_fin_year(dt: date):
    """Get financial year representation."""
    if dt.month > 3:
        year1, year2 = dt.year, dt.year + 1
    else:
        year1, year2 = dt.year - 1, dt.year

    if year1 % 100 != 99:
        year2 %= 100

    return f"FY{year1}-{year2}"
