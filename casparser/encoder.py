import datetime
import decimal
import json
from typing import Any


class CASDataEncoder(json.JSONEncoder):
    """
    CAS Data encoder class for json output
    """

    def default(self, o: Any) -> Any:
        if isinstance(o, decimal.Decimal):
            return str(o)
        elif isinstance(o, datetime.date):
            return o.isoformat()
        return super().default(o)
