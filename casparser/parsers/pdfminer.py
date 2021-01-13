import io
import re
from typing import List, Optional, Iterator, Union

from pdfminer.pdfparser import PDFParser
from pdfminer.pdfdocument import PDFDocument, PDFPasswordIncorrect, PDFSyntaxError
from pdfminer.layout import LAParams
from pdfminer.converter import PDFPageAggregator
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
from pdfminer.pdfpage import PDFPage
from pdfminer.layout import LTTextBoxHorizontal, LTTextBoxVertical

from casparser.enums import FileType
from casparser.exceptions import CASParseError
from .utils import is_close, InvestorInfo, PartialCASData


def parse_investor_info(layout, width, height) -> InvestorInfo:
    """Parse investor info."""
    text_elements = sorted(
        [
            x
            for x in layout
            if isinstance(x, LTTextBoxHorizontal) and x.x1 < width / 1.5 and x.y1 > height / 2
        ],
        key=lambda x: -x.y1,
    )
    email_found = False
    address_lines = []
    email = None
    mobile = None
    name = None
    for el in text_elements:
        txt = el.get_text().strip()
        if txt == "":
            continue
        if not email_found:
            if m := re.search(r"^\s*email\s+id\s*:\s*(.+?)(?:\s|$)", txt, re.I):
                email = m.group(1).strip()
                email_found = True
            continue
        if name is None:
            name = txt
        else:
            if (
                re.search(r"Date\s+Transaction|Folio\s+No|^Date\s*$", txt, re.I | re.MULTILINE)
                or mobile is not None
            ):
                return InvestorInfo(
                    email=email, name=name, mobile=mobile or "", address="\n".join(address_lines)
                )
            elif m := re.search(r"mobile\s*:\s*([+\d]+)(?:s|$)", txt, re.I):
                mobile = m.group(1).strip()
            address_lines.append(txt)
    raise CASParseError("Unable to parse investor data")


def detect_pdf_source(document) -> FileType:
    """
    Try to infer pdf source (CAMS/KFINTECH) from the pdf metadata.

    :param document: PDF document object
    :return: FileType
    """
    file_type = FileType.UNKNOWN
    for info in document.info:
        producer = info.get("Producer", b"").decode("utf8", "ignore").replace("\x00", "")
        if "Data Dynamics ActiveReports" in producer:
            file_type = FileType.KFINTECH
        elif "Stimulsoft Reports" in producer:
            file_type = FileType.CAMS
        if file_type != FileType.UNKNOWN:
            break
    return file_type


def group_similar_rows(elements_list: List[Iterator[LTTextBoxHorizontal]]):
    """
    Group `LTTextBoxHorizontal` elements having similar rows, with a tolerance.

    :param elements_list: List of elements from each page
    """
    lines = []
    for elements in elements_list:
        sorted_elements = list(sorted(elements, key=lambda x: (-x.y1, x.x0)))
        if len(sorted_elements) == 0:
            continue
        y0, y1 = sorted_elements[0].y0, sorted_elements[0].y1
        items = []
        for el in sorted_elements:
            if len(items) > 0 and not (is_close(el.y1, y1, tol=3) or is_close(el.y0, y0, tol=3)):
                line = "\t\t".join(
                    [x.get_text().strip() for x in sorted(items, key=lambda x: x.x0)]
                )
                if line.strip():
                    lines.append(line)
                items = []
                y0, y1 = el.y0, el.y1
            items.append(el)
    return lines


def cas_pdf_to_text(filename: Union[str, io.IOBase], password) -> PartialCASData:
    """
    Parse CAS pdf and returns line data.

    :param filename: CAS pdf file (CAMS or Kfintech)
    :param password: CAS pdf password
    :return: array of lines from the CAS.
    """
    file_type: Optional[FileType] = None

    if isinstance(filename, str):
        fp = open(filename, "rb")
    elif hasattr(filename, "read") and hasattr(filename, "close"):  # file-like object
        fp = filename
    else:
        raise CASParseError("Invalid input. filename should be a string or a file like object")

    with fp:
        pdf_parser = PDFParser(fp)
        try:
            document = PDFDocument(pdf_parser, password=password)
        except PDFPasswordIncorrect:
            raise CASParseError("Incorrect PDF password!")
        except PDFSyntaxError:
            raise CASParseError("Unhandled error while opening file")

        line_margin = {FileType.KFINTECH: 0.1, FileType.CAMS: 0.2}.get(
            detect_pdf_source(document), 0.2
        )

        rsrc_mgr = PDFResourceManager()
        laparams = LAParams(line_margin=line_margin, detect_vertical=True)
        device = PDFPageAggregator(rsrc_mgr, laparams=laparams)
        interpreter = PDFPageInterpreter(rsrc_mgr, device)

        pages: List[Iterator[LTTextBoxHorizontal]] = []

        investor_info = None
        for page in PDFPage.create_pages(document):
            interpreter.process_page(page)
            layout = device.get_result()
            text_elements = filter(lambda x: isinstance(x, LTTextBoxHorizontal), layout)
            if file_type is None:
                for el in filter(lambda x: isinstance(x, LTTextBoxVertical), layout):
                    if re.search("CAMSCASWS", el.get_text()):
                        file_type = FileType.CAMS
                    if re.search("KFINCASWS", el.get_text()):
                        file_type = FileType.KFINTECH
            if investor_info is None:
                investor_info = parse_investor_info(layout, *page.mediabox[2:])
            pages.append(text_elements)

        lines = group_similar_rows(pages)
        return PartialCASData(file_type=file_type, investor_info=investor_info, lines=lines)
