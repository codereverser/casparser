import re

from ..enums import CASFileType, FileType
from ..exceptions import CASParseError
from ..types import ProcessedCASData
from .cas_detailed import process_detailed_text
from .cas_summary import process_summary_text
from .nsdl_statement import process_nsdl_text
from .regex import CAS_TYPE_RE


def detect_cas_type(text):
    if m := re.search(CAS_TYPE_RE, text, re.DOTALL | re.MULTILINE | re.I):
        match = m.group(1).lower().strip()
        if match == "statement":
            return CASFileType.DETAILED
        elif match == "summary":
            return CASFileType.SUMMARY
    return CASFileType.UNKNOWN


def process_cas_text(text, file_type: FileType = FileType.UNKNOWN) -> ProcessedCASData:
    """
    Process the text version of a CAS pdf and return the detailed summary.
    :param text:
    :return:
    """
    if file_type == FileType.NSDL:
        return process_nsdl_text(text)
    cas_statement_type = detect_cas_type(text[:1000])
    if cas_statement_type == CASFileType.DETAILED:
        return process_detailed_text(text)
    elif cas_statement_type == CASFileType.SUMMARY:
        return process_summary_text(text)
    raise CASParseError("Unknown CAS file type")
