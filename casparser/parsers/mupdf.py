import copy
import io
import re
from operator import itemgetter
from typing import Any, Iterator, List, Union

# noinspection PyPackageRequirements
import fitz

from casparser.enums import FileType
from casparser.exceptions import CASParseError, IncorrectPasswordError
from casparser.types import InvestorInfo, PartialCASData

from .utils import is_close


def merge_bbox(bbox1, bbox2):
    """Merge two pdf blocks' bounding boxes."""
    return (
        min(bbox1[0], bbox2[0]),  # x0
        min(bbox1[1], bbox2[1]),  # y0
        max(bbox1[2], bbox2[2]),  # x1
        max(bbox1[3], bbox2[3]),  # y1
    )


def group_similar_blocks(blocks, file_type=FileType.UNKNOWN, mode="vertical"):
    """Group overlapping blocks in a page."""
    grouped_blocks = []
    curr_y0 = -1
    curr_y1 = -1
    blocks = copy.deepcopy(blocks)

    if mode == "vertical":
        blocks = sorted(blocks, key=lambda x: (x["bbox"][0], x["bbox"][1]))
    else:
        blocks = sorted(blocks, key=lambda x: (x["bbox"][1], x["bbox"][0]))

    for block in blocks:
        x0, y0, x1, y1 = block["bbox"]
        if abs(y1 - y0) > abs(x1 - x0) * 4:
            # Ignore vertical elements. No useful info there.
            continue
        if (is_close(y0, curr_y0, 0.1) or curr_y0 <= y0 <= y1 <= curr_y1) and len(
            grouped_blocks
        ) > 0:
            new_block = grouped_blocks.pop()
            new_block["lines"].extend(block["lines"])
            new_block["bbox"] = merge_bbox(new_block["bbox"], block["bbox"])
        else:
            new_block = block
        grouped_blocks.append(new_block)
        curr_y0 = new_block["bbox"][1]
        curr_y1 = new_block["bbox"][3]

    if mode == "vertical":
        grouped_blocks = group_similar_blocks(
            grouped_blocks, file_type=file_type, mode="horizontal"
        )

    return grouped_blocks


def extract_blocks(page_dict, file_type=FileType.UNKNOWN):
    """Extract text blocks from page dictionary.
    The logic is similar to `PyMuPDF.TextPage.extractBLOCKS` but with a slightly better text
    arrangement.
    """
    tolerance = {FileType.CAMS: 3, FileType.KFINTECH: 3, FileType.CDSL: 7, FileType.NSDL: 7}.get(
        file_type, 3
    )

    blocks = []
    grouped_blocks = group_similar_blocks(page_dict.get("blocks", []), file_type=file_type)
    for num, block in enumerate(grouped_blocks):
        lines = []
        items = []
        bbox = [0, 0, 0, 0]
        if len(block.get("lines", [])) > 0:
            bbox = block["lines"][0]["bbox"]
        y0, y1 = bbox[1], bbox[3]
        if file_type in (FileType.NSDL, FileType.CDSL):
            block["lines"] = sorted(block["lines"], key=lambda x: (x["bbox"][0], x["bbox"][1]))
        else:
            block["lines"] = sorted(block["lines"], key=lambda x: x["bbox"][1])
        for line in block["lines"]:
            if len(items) > 0 and not (
                is_close(y0, line["bbox"][1], tol=tolerance)
                or is_close(y1, line["bbox"][3], tol=tolerance)
                or is_close(y1, line["bbox"][1], tol=2)
                or is_close(y0, line["bbox"][3], tol=2)
                or y0 <= line["bbox"][1] <= line["bbox"][3] <= y1
            ):
                full_text = "\t\t".join(
                    [x[0].strip() for x in sorted(items, key=lambda x: x[1][0]) if x[0].strip()]
                )
                if full_text.strip():
                    lines.append(full_text)
                items = []
                # y0, y1 = line["bbox"][1], line["bbox"][3]
                y0, y1 = min(y0, line["bbox"][1]), max(y1, line["bbox"][3])
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
    for block in sorted(blocks, key=lambda x: -x["bbox"][1]):
        block_str = str(block)
        if re.search("CAMSCASWS", block_str):
            return FileType.CAMS
        elif re.search("KFINCASWS", block_str):
            return FileType.KFINTECH
        elif "NSDL Consolidated Account Statement" in block_str or "About NSDL" in block_str:
            return FileType.NSDL
        elif "Central Depository Services (India) Limited" in block_str:
            return FileType.CDSL
    return FileType.UNKNOWN


def parse_investor_info_dp(page_dict, page_rect: fitz.Rect) -> InvestorInfo:
    """Parse investor info."""
    width = max(page_rect.width, 600)
    height = max(page_rect.height, 800)

    blocks = sorted(
        [x for x in page_dict["blocks"] if x["bbox"][1] < height / 2], key=lambda x: x["bbox"][1]
    )

    address_lines = []
    email = ""
    mobile = None
    name = None
    cas_id_found = False
    for block in blocks:
        for line in block["lines"]:
            for span in filter(
                lambda x: x["bbox"][0] <= width / 2 and x["text"].strip() != "", line["spans"]
            ):
                txt = span["text"].strip()
                if not cas_id_found:
                    if m := re.search(r"[CAS|NSDL]\s+ID\s*:\s*(.+?)(?:\s|$)", txt, re.I):
                        # email = m.group(1).strip()
                        cas_id_found = True
                    continue
                if name is None:
                    name = txt
                else:
                    if (
                        re.search(
                            r"Statement\s+for\s+the\s+period|Your\s+demat\s+account\s+and\s+mutual\s+fund",
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


def parse_investor_info(page_dict, page_rect: fitz.Rect) -> InvestorInfo:
    """Parse investor info."""
    width = max(page_rect.width, 600)
    height = max(page_rect.height, 800)

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
            for span in filter(
                lambda x: x["bbox"][0] <= width / 3 and x["text"].strip() != "", line["spans"]
            ):
                txt = span["text"].strip()
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
                            r"Mutual\s+Fund|Date\s+Transaction|Folio\s+No|^Date\s*$",
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
        sorted_elements = list(sorted(elements, key=itemgetter(1, 0)))
        y0, y1 = 0, 0
        if len(sorted_elements) > 0:
            y0, y1 = sorted_elements[0][1], sorted_elements[0][3]
        items = []
        for el in sorted_elements:
            x2, y2, x3, y3 = el[:4]
            if abs(y3 - y2) > abs(x3 - x2):
                # Ignore vertical elements. No useful info there.
                continue
            if len(items) > 0 and not (
                is_close(y3, y1, tol=2) or is_close(y2, y0, tol=2) or y0 <= y2 <= y3 <= y1
            ):
                line = "\t\t".join(
                    [x[4].strip() for x in sorted(items, key=lambda x: x[0]) if x[4].strip()]
                )
                if line.strip():
                    lines.append(line)
                items = []
                y0, y1 = el[1], el[3]
            items.append(el)
        if len(items) > 0:
            line = "\t\t".join([x[4].strip() for x in sorted(items, key=lambda x: x[0])])
            if line.strip():
                lines.append(line)
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
            doc = fitz.Document(stream=fp.read(), filetype="pdf")
        except Exception as e:
            raise CASParseError("Unhandled error while opening file :: %s" % (str(e)))

        if doc.needs_pass:
            rc = doc.authenticate(password)
            if not rc:
                raise IncorrectPasswordError("Incorrect PDF password!")

        pages = []
        investor_info = None

        for page_num, page in enumerate(doc):
            text_page = page.get_textpage()
            page_dict = text_page.extractDICT(sort=True)
            if file_type == FileType.UNKNOWN:
                file_type = parse_file_type(page_dict["blocks"])
            blocks = extract_blocks(page_dict, file_type=file_type)
            sorted_blocks = sorted(blocks, key=itemgetter(1, 0))
            if investor_info is None:
                if file_type in (FileType.CAMS, FileType.KFINTECH):
                    investor_info = parse_investor_info(page_dict, page.rect)
                elif file_type in (FileType.NSDL, FileType.CDSL) and page_num == 1:
                    investor_info = parse_investor_info_dp(page_dict, page.rect)
            if file_type == FileType.NSDL and page_num == 0:
                # Ignore first page. no useful data
                continue
            pages.append(sorted_blocks)
        lines = group_similar_rows(pages)
        return PartialCASData(file_type=file_type, investor_info=investor_info, lines=lines)
