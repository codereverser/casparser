from pdfminer.layout import LTTextBoxHorizontal
import pytest

from casparser.exceptions import CASParseError
from .base import BaseTestClass


class TestPDFMiner(BaseTestClass):
    """Test pdfminer parser."""

    @classmethod
    def setup_class(cls):
        BaseTestClass.setup_class()
        cls.mode = "pdfminer"

    def test_bad_investor_info(self):
        from casparser.parsers.pdfminer import parse_investor_info

        with pytest.raises(CASParseError) as exc_info:
            box = LTTextBoxHorizontal()
            box.get_text()
            parse_investor_info([], 0, 0)
        assert "Unable to parse investor data" in str(exc_info)
