import re

from ..enums import CASFileType
from ..exceptions import CASParseError
from .cas_detailed import process_detailed_text
from .cas_summary import process_summary_text
from .regex import CAS_TYPE_RE


def detect_cas_type(text):
    if m := re.search(CAS_TYPE_RE, text, re.DOTALL | re.MULTILINE | re.I):
        match = m.group(1).lower().strip()
        if match == "statement":
            return CASFileType.DETAILED
        elif match == "summary":
            return CASFileType.SUMMARY
    return CASFileType.UNKNOWN


def process_cas_text(text):
    """
    Process the text version of a CAS pdf and return the detailed summary.
    :param text:
    :return:
    """
    cas_file_type = detect_cas_type(text[:1000])
    if cas_file_type == CASFileType.DETAILED:
        return process_detailed_text(text)
    elif cas_file_type == CASFileType.SUMMARY:
        return process_summary_text(text)
    raise CASParseError("Unknown CAS file type")
