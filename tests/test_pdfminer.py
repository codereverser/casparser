import sys

import pytest
from pdfminer.layout import LTTextBoxHorizontal

from casparser import read_cas_pdf
from casparser.exceptions import CASParseError

from .base import BaseTestClass

try:
    import builtins
except ImportError:
    import __builtin__ as builtins

realimport = builtins.__import__


def mockimport(name, *args):
    """Force ImportError on fitz and/or mupdf import and make casparser fallback to pdfminer"""
    if name in ("fitz", "mupdf"):
        raise ImportError
    return realimport(name, *args)


@pytest.fixture(scope="class")
def monkeyclass():
    with pytest.MonkeyPatch.context() as mp:
        yield mp


@pytest.fixture(scope="class")
def use_pdfminer(monkeyclass):
    if "fitz" in sys.modules:
        del sys.modules["fitz"]
    monkeyclass.setattr(builtins, "__import__", mockimport)
    yield
    monkeyclass.setattr(builtins, "__import__", realimport)


@pytest.mark.usefixtures("use_pdfminer")
class TestPDFMiner(BaseTestClass):
    """Test pdfminer parser."""

    @classmethod
    def setup_class(cls):
        BaseTestClass.setup_class()

    def test_bad_investor_info(self):
        from casparser.parsers.pdfminer import parse_investor_info

        with pytest.raises(CASParseError) as exc_info:
            box = LTTextBoxHorizontal()
            box.get_text()
            parse_investor_info([], 0, 0)
        assert "Unable to parse investor data" in str(exc_info)

    def test_invalid_file_type(self):
        with pytest.raises(CASParseError) as exc_info:
            read_cas_pdf(1, "", force_pdfminer=True)
        assert "Invalid input" in str(exc_info)
