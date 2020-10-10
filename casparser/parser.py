import json
import re
from typing import List, Optional, Iterator

import numpy as np
from pdfminer.pdfparser import PDFParser
from pdfminer.pdfdocument import PDFDocument
from pdfminer.layout import LAParams
from pdfminer.converter import PDFPageAggregator
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
from pdfminer.pdfpage import PDFPage
from pdfminer.layout import LTTextBoxHorizontal, LTTextBoxVertical

from .encoder import CASDataEncoder
from .enums import FileType
from .process import process_cas_text


def group_similar_rows(elements_list: List[Iterator[LTTextBoxHorizontal]]):
    """
    Group `LTTextBoxHorizontal` elements having similar rows, with a tolerance

    :param elements_list: List of elements from each page
    """
    lines = []
    for elements in elements_list:
        sorted_elements = list(sorted(elements, key=lambda x: (-x.y1, x.x0)))
        if len(sorted_elements) == 0:
            continue
        y1 = sorted_elements[0].y1
        items = []
        for el in sorted_elements:
            if len(items) > 0 and not (np.isclose(el.y1, y1, atol=3)):
                lines.append('\t\t'.join([x.get_text().strip() for x in
                                          sorted(items, key=lambda x: x.x0)]))
                items = []
                y1 = el.y1
            items.append(el)
    return lines


def read_cas_pdf(filename, password, output='dict'):
    """
    Parses CAS pdf and returns line data.

    :param filename: CAS pdf file (CAMS or Karvy)
    :param password: CAS pdf password
    :param output: Output format (json,dict)  [default: dict]
    :return: array of lines from the CAS.
    """
    file_type: Optional[FileType]= None

    with open(filename, 'rb') as fp:
        pdf_parser = PDFParser(fp)
        document = PDFDocument(pdf_parser, password=password)

        rsrc_mgr = PDFResourceManager()
        laparams = LAParams(line_margin=0.2, detect_vertical=True)
        device = PDFPageAggregator(rsrc_mgr, laparams=laparams)
        interpreter = PDFPageInterpreter(rsrc_mgr, device)

        pages: List[Iterator[LTTextBoxHorizontal]] = []

        for page in PDFPage.create_pages(document):
            interpreter.process_page(page)
            layout = device.get_result()
            text_elements = filter(lambda x: isinstance(x, LTTextBoxHorizontal), layout)
            if file_type is None:
                for el in filter(lambda x: isinstance(x, LTTextBoxVertical), layout):
                    if re.search('CAMSCASWS', el.get_text()):
                        file_type = FileType.CAMS
                    elif re.search('KFINCASWS', el.get_text()):
                        file_type = FileType.KARVY
            pages.append(text_elements)

        processed_data = process_cas_text('\u2029'.join(group_similar_rows(pages)))
        processed_data['file_type'] = file_type

        # TODO: Add Validation (calculated close vs reported)
        if output == 'dict':
            return processed_data
        else:
            return json.dumps(processed_data, cls=CASDataEncoder)
