import csv
import io

from casparser.types import CASData


def is_close(a0, a1, tol=1.0e-4):
    """
    Check if two elements are almost equal with a tolerance.

    :param a0: number to compare
    :param a1: number to compare
    :param tol: The absolute tolerance
    :return: Returns true if the values are almost equal
    """
    return abs(a0 - a1) < tol


def cas2json(data: CASData) -> str:
    return data.model_dump_json(by_alias=True)


def cas2csv_summary(data: CASData) -> str:
    with io.StringIO() as csv_fp:
        header = [
            "amc",
            "folio",
            "advisor",
            "registrar",
            "pan",
            "scheme",
            "isin",
            "amfi",
            "open",
            "close",
            "value",
            "date",
            "transactions",
        ]
        writer = csv.DictWriter(csv_fp, fieldnames=header)
        writer.writeheader()
        current_amc = None
        for folio in data.folios:
            if current_amc != folio.amc:
                current_amc = folio.amc
            for scheme in folio.schemes:
                row = {
                    "amc": current_amc.replace("\n", " "),
                    "folio": folio.folio,
                    "advisor": scheme.advisor,
                    "registrar": scheme.rta,
                    "pan": folio.PAN,
                    "scheme": scheme.scheme.replace("\n", " "),
                    "isin": scheme.isin,
                    "amfi": scheme.amfi,
                    "open": scheme.open,
                    "close": scheme.close,
                    "value": scheme.valuation.value,
                    "date": scheme.valuation.date,
                    "transactions": len(scheme.transactions),
                }
                writer.writerow(row)
        csv_fp.seek(0)
        csv_data = csv_fp.read()
        return csv_data


def cas2csv(data: CASData) -> str:
    with io.StringIO() as csv_fp:
        header = [
            "amc",
            "folio",
            "pan",
            "scheme",
            "advisor",
            "isin",
            "amfi",
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
        for folio in data.folios:
            if current_amc != folio.amc:
                current_amc = folio.amc
            for scheme in folio.schemes:
                for transaction in scheme.transactions:
                    row = {
                        "amc": current_amc.replace("\n", " "),
                        "folio": folio.folio,
                        "pan": folio.PAN,
                        "scheme": scheme.scheme.replace("\n", " "),
                        "advisor": scheme.advisor,
                        "isin": scheme.isin,
                        "amfi": scheme.amfi,
                        "date": transaction.date,
                        "description": transaction.description.replace("\n", " "),
                        "amount": transaction.amount,
                        "units": transaction.units,
                        "nav": transaction.nav,
                        "balance": transaction.balance,
                        "type": transaction.type,
                        "dividend": transaction.dividend_rate,
                    }
                    writer.writerow(row)
        csv_fp.seek(0)
        csv_data = csv_fp.read()
        return csv_data
