from collections import namedtuple
import csv
import io
import json

from ..encoder import CASDataEncoder
from ..types import CASParserDataType

InvestorInfo = namedtuple("InvestorInfo", ["name", "email", "address", "mobile"])
PartialCASData = namedtuple("PartialCASData", ["file_type", "investor_info", "lines"])


def is_close(a0, a1, tol=1.0e-4):
    """
    Check if two elements are almost equal with a tolerance.

    :param a0: number to compare
    :param a1: number to compare
    :param tol: The absolute tolerance
    :return: Returns boolean value if the values are almost equal
    """
    return abs(a0 - a1) < tol


def cas2json(data: CASParserDataType) -> str:
    return json.dumps(data, cls=CASDataEncoder)


def cas2csv(data: CASParserDataType) -> str:
    with io.StringIO() as csv_fp:
        header = [
            "amc",
            "folio",
            "pan",
            "scheme",
            "date",
            "description",
            "amount",
            "units",
            "nav",
            "balance",
            "type",
            "dividend",
        ]
        writer = csv.DictWriter(csv_fp, fieldnames=header)
        writer.writeheader()
        current_amc = None
        for folio in data.get("folios", []):
            if current_amc != folio.get("amc", ""):
                current_amc = folio["amc"]
            for scheme in folio["schemes"]:
                for transaction in scheme["transactions"]:
                    row = {
                        "amc": current_amc.replace("\n", " "),
                        "folio": folio["folio"],
                        "pan": folio["PAN"],
                        "scheme": scheme["scheme"].replace("\n", " "),
                        "date": transaction["date"],
                        "description": transaction["description"].replace("\n", " "),
                        "amount": transaction["amount"],
                        "units": transaction["units"],
                        "nav": transaction["nav"],
                        "balance": transaction["balance"],
                        "type": transaction["type"],
                        "dividend": transaction["dividend_rate"],
                    }
                    writer.writerow(row)
        csv_fp.seek(0)
        csv_data = csv_fp.read()
        return csv_data
