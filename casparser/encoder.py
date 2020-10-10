import datetime
import decimal
import json
from typing import Any


class CASDataEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, decimal.Decimal):
            return str(o)
        elif isinstance(o, (datetime.date, datetime.date)):
            return o.isoformat()
        return super().default(o)
