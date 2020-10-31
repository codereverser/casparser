import io
import os

import pytest

from casparser.exceptions import CASParseError


class BaseTestClass:
    """Common test cases for all available parsers."""
    
    @classmethod
    def setup_class(cls):
        cls.mode = "mupdf"
        cls.cams_file_name = os.getenv("CAMS_CAS_FILE")
        cls.kfintech_file_name = os.getenv("KFINTECH_CAS_FILE")
        cls.bad_file_name = os.getenv("BAD_CAS_FILE")
        cls.cams_password = os.getenv("CAMS_CAS_PASSWORD")
        cls.kfintech_password = os.getenv("KFINTECH_CAS_PASSWORD")

    def read_pdf(self, filename, password, output="dict"):
        if self.mode == "pdfminer":
            from casparser.parsers.pdfminer import read_cas_pdf
        else:
            from casparser.parsers.mupdf import read_cas_pdf

        return read_cas_pdf(filename, password, output=output)

    def test_read_dict(self):
        self.read_pdf(self.cams_file_name, self.cams_password)
        self.read_pdf(self.kfintech_file_name, self.kfintech_password)

    def test_read_json(self):
        self.read_pdf(self.cams_file_name, self.cams_password, output="json")
        self.read_pdf(self.kfintech_file_name, self.kfintech_password, output="json")

    def test_invalid_password(self):
        with pytest.raises(CASParseError) as exc_info:
            self.read_pdf(self.cams_file_name, "")
        assert "Incorrect PDF password!" in str(exc_info)

    def test_invalid_file(self):
        with pytest.raises(CASParseError) as exc_info, io.BytesIO(b"test") as fp:
            self.read_pdf(fp, "")
        assert "Unhandled error while opening" in str(exc_info)

    def test_invalid_file_type(self):
        with pytest.raises(CASParseError) as exc_info:
            self.read_pdf(1, "")
        assert "Invalid input" in str(exc_info)
