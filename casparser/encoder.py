import datetime
import decimal
import json
from typing import Any

from .enums import FileType


class CASDataEncoder(json.JSONEncoder):
    """
    CAS Data encoder class for json output
    """

    def default(self, o: Any) -> Any:
        if isinstance(o, decimal.Decimal):
            return str(o)
        elif isinstance(o, (datetime.date, datetime.date)):
            return o.isoformat()
        elif isinstance(o, FileType):
            return o.name
        return super().default(o)
