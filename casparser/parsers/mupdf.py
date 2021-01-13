import copy
import io
from operator import itemgetter
import re
from typing import List, Iterator, Union, Any

# noinspection PyPackageRequirements
import fitz

from casparser.enums import FileType
from casparser.exceptions import CASParseError
from .utils import is_close, InvestorInfo, PartialCASData


def merge_bbox(bbox1, bbox2):
    """Merge two pdf blocks' bounding boxes."""
    return (
        min(bbox1[0], bbox2[0]),  # x0
        min(bbox1[1], bbox2[1]),  # y0
        max(bbox1[2], bbox2[2]),  # x1
        max(bbox1[3], bbox2[3]),  # y1
    )


def group_similar_blocks(blocks):
    """Group overlapping blocks in a page."""
    grouped_blocks = []
    curr_y0 = -1
    blocks = copy.deepcopy(blocks)
    for block in blocks:
        y0 = block["bbox"][1]
        if is_close(y0, curr_y0, 0.1) and len(grouped_blocks) > 0:
            new_block = grouped_blocks.pop()
            new_block["lines"].extend(block["lines"])
            new_block["bbox"] = merge_bbox(new_block["bbox"], block["bbox"])
        else:
            new_block = block
        grouped_blocks.append(new_block)
        curr_y0 = new_block["bbox"][1]
    return grouped_blocks


def extract_blocks(page_dict):
    """Extract text blocks from page dictionary.
    The logic is similar to `PyMuPDF.TextPage.extractBLOCKS` but with a slightly better text
    arrangement.
    """
    blocks = []
    grouped_blocks = group_similar_blocks(page_dict.get("blocks", []))
    for block in grouped_blocks:
        lines = []
        items = []
        if len(block.get("lines", [])) == 0:
            continue
        bbox = block["lines"][0]["bbox"]
        y0, y1 = bbox[1], bbox[3]
        for line in sorted(block["lines"], key=lambda x: x["bbox"][1]):
            if len(items) > 0 and not (
                is_close(y0, line["bbox"][1], tol=3) or is_close(y1, line["bbox"][3], tol=3)
            ):
                full_text = "\t\t".join(
                    [x[0].strip() for x in sorted(items, key=lambda x: x[1][0]) if x[0].strip()]
                )
                if full_text.strip():
                    lines.append(full_text)
                items = []
                y0, y1 = line["bbox"][1], line["bbox"][3]
            line_text = "\t\t".join(
                [
                    span["text"].strip()
                    for span in sorted(line["spans"], key=lambda x: (x["origin"][0]))
                    if span["text"].strip()
                ]
            )
            items.append([line_text, line["bbox"]])
        if len(items) > 0:
            full_text = "\t\t".join(
                [x[0] for x in sorted(items, key=lambda x: x[1][0]) if x[0].strip()]
            )
            if full_text.strip():
                lines.append(full_text)
        text = "\n".join(lines)
        if text.strip() != "":
            blocks.append([*block["bbox"], text])
    return blocks


def parse_file_type(blocks):
    """Parse file type."""
    for block in sorted(blocks, key=lambda x: -x[1]):
        if re.search("CAMSCASWS", block[4]):
            return FileType.CAMS
        if re.search("KFINCASWS", block[4]):
            return FileType.KFINTECH
    return FileType.UNKNOWN


def parse_investor_info(page_dict) -> InvestorInfo:
    """Parse investor info."""
    width = max(page_dict["width"], 600)
    height = max(page_dict["height"], 800)

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
                txt = span["text"].strip()
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
                        re.search(
                            r"Date\s+Transaction|Folio\s+No|^Date\s*$",
                            txt,
                            re.I | re.MULTILINE,
                        )
                        or mobile is not None
                    ):
                        return InvestorInfo(
                            email=email,
                            name=name,
                            mobile=mobile or "",
                            address="\n".join(address_lines),
                        )
                    elif m := re.search(r"mobile\s*:\s*([+\d]+)(?:s|$)", txt, re.I):
                        mobile = m.group(1).strip()
                    address_lines.append(txt)
    raise CASParseError("Unable to parse investor data")


def group_similar_rows(elements_list: List[Iterator[Any]]):
    """
    Group elements having similar rows, with a tolerance.

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
            if len(items) > 0 and not (is_close(el[3], y1, tol=2) or is_close(el[1], y0, tol=2)):
                line = "\t\t".join(
                    [x[4].strip() for x in sorted(items, key=lambda x: x[0]) if x[4].strip()]
                )
                if line.strip():
                    lines.append(line)
                items = []
                y0, y1 = el[1], el[3]
            items.append(el)
    return lines


def cas_pdf_to_text(filename: Union[str, io.IOBase], password) -> PartialCASData:
    """
    Parse CAS pdf and returns line data.

    :param filename: CAS pdf file (CAMS or Kfintech)
    :param password: CAS pdf password
    :return: partial cas data with FileType, InvestorInfo and lines of data
    """
    file_type: FileType = FileType.UNKNOWN

    if isinstance(filename, str):
        fp = open(filename, "rb")
    elif hasattr(filename, "read") and hasattr(filename, "close"):  # file-like object
        fp = filename
    else:
        raise CASParseError("Invalid input. filename should be a string or a file like object")

    with fp:
        try:
            doc = fitz.open(stream=fp.read(), filetype="pdf")
        except Exception as e:
            raise CASParseError("Unhandled error while opening file :: %s" % (str(e)))

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
        return PartialCASData(file_type=file_type, investor_info=investor_info, lines=lines)
