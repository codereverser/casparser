import io
from typing import Union

from casparser.process import process_cas_text
from casparser.types import CASData, NSDLCASData, ProcessedCASData

from .utils import cas2csv, cas2json


def read_cas_pdf(
    filename: Union[str, io.IOBase],
    password,
    output="dict",
    sort_transactions=True,
    force_pdfminer=False,
):
    """
    Parse CAS pdf and returns line data.

    :param filename: CAS pdf file (CAMS or Kfintech)
    :param password: CAS pdf password
    :param output: Output format (json,dict)  [default: dict]
    :param sort_transactions: Sort transactions by date and re-compute balances.
    :param force_pdfminer: Force pdfminer parser even if mupdf is detected
    """
    if force_pdfminer:
        from .pdfminer import cas_pdf_to_text
    else:
        try:
            from .mupdf import cas_pdf_to_text
        except (ImportError, ModuleNotFoundError):
            from .pdfminer import cas_pdf_to_text

    partial_cas_data = cas_pdf_to_text(filename, password)
    processed_data = process_cas_text(
        "\u2029".join(partial_cas_data.lines), partial_cas_data.file_type
    )
    if isinstance(processed_data, ProcessedCASData):
        if sort_transactions:
            for folio in processed_data.folios:
                for idx, scheme in enumerate(folio.schemes):
                    dates = [x.date for x in scheme.transactions]
                    sorted_dates = list(sorted(dates))
                    if dates != sorted_dates:
                        sorted_transactions = []
                        balance = scheme.open
                        for transaction in sorted(scheme.transactions, key=lambda x: x.date):
                            balance += transaction.units or 0
                            transaction.balance = balance
                            sorted_transactions.append(transaction)
                        scheme.transactions = sorted_transactions
                    folio.schemes[idx] = scheme

        final_data = CASData(
            statement_period=processed_data.statement_period,
            folios=processed_data.folios,
            investor_info=partial_cas_data.investor_info,
            cas_type=processed_data.cas_type,
            file_type=partial_cas_data.file_type,
        )
    else:
        final_data = NSDLCASData(
            statement_period=processed_data.statement_period,
            accounts=processed_data.accounts,
            investor_info=partial_cas_data.investor_info,
            file_type=partial_cas_data.file_type,
        )
    if output == "dict":
        return final_data
    elif output == "csv":
        return cas2csv(final_data)
    return cas2json(final_data)
