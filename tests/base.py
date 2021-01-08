import io
import os
import re

from click.testing import CliRunner
import pytest

from casparser import read_cas_pdf
from casparser.exceptions import CASParseError


class BaseTestClass:
    """Common test cases for all available parsers."""

    @classmethod
    def setup_class(cls):
        cls.mode = "mupdf"
        cls.cams_file_name = os.getenv("CAMS_CAS_FILE")
        cls.cams_summary_file_name = os.getenv("CAMS_CAS_SUMMARY")
        cls.kfintech_file_name = os.getenv("KFINTECH_CAS_FILE")
        cls.bad_file_name = os.getenv("BAD_CAS_FILE")
        cls.cams_password = os.getenv("CAMS_CAS_PASSWORD")
        cls.kfintech_password = os.getenv("KFINTECH_CAS_PASSWORD")

    def read_pdf(self, filename, password, output="dict"):
        use_pdfminer = self.mode == "pdfminer"
        return read_cas_pdf(filename, password, output=output, force_pdfminer=use_pdfminer)

    def test_read_summary(self):
        data = self.read_pdf(self.cams_summary_file_name, self.cams_password)
        assert len(data.get("folios", [])) == 4
        assert data["cas_type"] == "SUMMARY"

    def test_read_dict(self):
        from casparser.cli import cli

        pdf_files = [
            (self.cams_file_name, self.cams_password),
            (self.kfintech_file_name, self.kfintech_password),
        ]

        runner = CliRunner()

        for pdf_file, pdf_password in pdf_files:
            args = [pdf_file, "-p", pdf_password]
            if self.mode != "mupdf":
                args.append("--force-pdfminer")
            result = runner.invoke(cli, args)
            assert result.exit_code == 0
            assert "Statement Period:" in result.output
            assert re.search(r"Matched\s+:\s+8\s+schemes", result.output) is not None
            assert re.search(r"Error\s+:\s+0\s+schemes", result.output) is not None

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
