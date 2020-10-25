from collections import namedtuple
import io
import json
from operator import itemgetter
import re
from typing import List, Iterator, Union, Any

# noinspection PyPackageRequirements
import fitz

from casparser.encoder import CASDataEncoder
from casparser.enums import FileType
from casparser.exceptions import CASParseError
from casparser.process import process_cas_text
from .utils import isclose

InvestorInfo = namedtuple("InvestorInfo", ["name", "email", "address", "mobile"])


def extract_blocks(page_dict):
    """Extract text blocks from page dictionary.
    The logic is similar to `PyMuPDF.TextPage.extractBLOCKS` but with a slightly better text
    arrangement.
    """
    blocks = []
    for block in page_dict.get("blocks", []):
        lines = []
        items = []
        if len(block.get("lines", [])) == 0:
            continue
        bbox = block["lines"][0]["bbox"]
        y0, y1 = bbox[1], bbox[3]
        for line in sorted(block["lines"], key=lambda x: x["bbox"][1]):
            if len(items) > 0 and not (
                    isclose(y0, line["bbox"][1], tol=3) or isclose(y1, line["bbox"][3], tol=3)
            ):
                full_text = "\t\t".join([x[0] for x in sorted(items, key=lambda x: x[1][0])])
                if full_text.strip():
                    lines.append(full_text)
                items = []
                y0, y1 = line["bbox"][1], line["bbox"][3]
            line_text = "\t\t".join(
                [span["text"] for span in sorted(line["spans"], key=lambda x: (x["origin"][0]))]
            )
            items.append([line_text, line["bbox"]])
        if len(items) > 0:
            full_text = "\t\t".join([x[0] for x in sorted(items, key=lambda x: x[1][0])])
            if full_text.strip():
                lines.append(full_text)
        text = "\n".join(lines)
        if text.strip() != "":
            blocks.append([*block["bbox"], text])
    return blocks


def parse_file_type(blocks):
    for block in sorted(blocks, key=lambda x: -x[1]):
        if re.search("CAMSCASWS", block[4]):
            return FileType.CAMS
        elif re.search("KFINCASWS", block[4]):
            return FileType.KFINTECH


def parse_investor_info(page_dict) -> InvestorInfo:
    width = page_dict["width"]
    height = page_dict["height"]
    blocks = sorted(
        [x for x in page_dict["blocks"] if x["bbox"][1] < height / 2], key=lambda x: x["bbox"][1]
    )

    email_found = False
    address_lines = []
    email = None
    mobile = None
    name = None
    for block in blocks:
        for line in block["lines"]:
            for span in line["spans"]:
                if span["bbox"][0] > width / 3:
                    continue
                txt = span["text"]
                if txt == "":
                    continue
                if not email_found:
                    if m := re.search(r"^\s*email\s+id\s*:\s*(.+?)(?:\s|$)", txt, re.I):
                        email = m.group(1).strip()
                        email_found = True
                    continue
                elif name is None:
                    name = txt
                else:
                    if m := re.search(r"mobile\s*:\s*([+\d]+)(?:s|$)", txt, re.I):
                        mobile = m.group(1).strip()
                    address_lines.append(txt)
                    if mobile is not None:
                        return InvestorInfo(
                            email=email, name=name, mobile=mobile, address="\n".join(address_lines)
                        )
    if email is None or mobile is None:
        raise CASParseError("Unable to parse investor data")
    return InvestorInfo(email=email, name=name, mobile=mobile, address="\n".join(address_lines))


def group_similar_rows(elements_list: List[Iterator[Any]]):
    """
    Group elements having similar rows, with a tolerance

    :param elements_list: List of elements from each page
    """
    lines = []
    for elements in elements_list:
        sorted_elements = list(sorted(elements, key=itemgetter(3, 0)))
        if len(sorted_elements) == 0:
            continue
        y0, y1 = sorted_elements[0][1], sorted_elements[0][3]
        items = []
        for el in sorted_elements:
            if len(items) > 0 and not (isclose(el[3], y1, tol=3) or isclose(el[1], y0, tol=3)):
                line = "\t\t".join([x[4] for x in sorted(items, key=lambda x: x[0])])
                if line.strip():
                    lines.append(line)
                items = []
                y0, y1 = el[1], el[3]
            items.append(el)
    return lines


def read_cas_pdf(filename: Union[str, io.IOBase], password, output="dict"):
    """
    Parses CAS pdf and returns line data.

    :param filename: CAS pdf file (CAMS or Kfintech)
    :param password: CAS pdf password
    :param output: Output format (json,dict)  [default: dict]
    :return: array of lines from the CAS.
    """
    file_type: FileType = FileType.UNKNOWN

    if isinstance(filename, str):
        fp = open(filename, "rb")
    elif isinstance(filename, io.IOBase):
        fp = filename
    elif hasattr(filename, "read"):  # compatibility for Django UploadedFile
        fp = filename
    else:
        raise CASParseError("Invalid input. filename should be a string or a file like object")

    with fp:
        try:
            doc = fitz.open(stream=fp.read(), filetype="pdf")
        except Exception as e:
            raise CASParseError("Unhandled error while opening file :: %s" % (str(e)))

        if not doc.isPDF:
            raise CASParseError("Input file is not PDF")

        if doc.needsPass:
            rc = doc.authenticate(password)
            if not rc:
                raise CASParseError("Incorrect PDF password!")

        pages = []
        investor_info = None

        for page in doc:
            text_page = page.getTextPage()
            page_dict = text_page.extractDICT()
            blocks = extract_blocks(page_dict)
            if file_type == FileType.UNKNOWN:
                file_type = parse_file_type(blocks)
            sorted_blocks = sorted(blocks, key=itemgetter(1, 0))
            if investor_info is None:
                investor_info = parse_investor_info(page_dict)
            pages.append(sorted_blocks)
        lines = group_similar_rows(pages)
        processed_data = process_cas_text("\u2029".join(lines))
        processed_data.update(
            {
                "file_type": file_type.name,
                "investor_info": investor_info._asdict(),
            }
        )
        if output == "dict":
            return processed_data
        else:
            return json.dumps(processed_data, cls=CASDataEncoder)
