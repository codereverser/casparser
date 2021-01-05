import io
from typing import Union

from casparser.process import process_cas_text
from .utils import cas2json, cas2csv


def read_cas_pdf(filename: Union[str, io.IOBase], password, output="dict", force_pdfminer=False):
    """
    Parse CAS pdf and returns line data.

    :param filename: CAS pdf file (CAMS or Kfintech)
    :param password: CAS pdf password
    :param output: Output format (json,dict)  [default: dict]
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

    processed_data = process_cas_text("\u2029".join(partial_cas_data.lines))
    # noinspection PyProtectedMember
    processed_data.update(
        {
            "file_type": partial_cas_data.file_type.name,
            "investor_info": partial_cas_data.investor_info._asdict(),
        }
    )
    if output == "dict":
        return processed_data
    elif output == "csv":
        return cas2csv(processed_data)
    return cas2json(processed_data)
