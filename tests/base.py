import io
import json
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

        cls.pdf_files = [
            (cls.cams_file_name, cls.cams_password),
            (cls.kfintech_file_name, cls.kfintech_password),
        ]

    def read_pdf(self, filename, password, output="dict"):
        use_pdfminer = self.mode == "pdfminer"
        return read_cas_pdf(filename, password, output=output, force_pdfminer=use_pdfminer)

    def test_output_json(self):
        for filename, password in self.pdf_files:
            json_data = self.read_pdf(filename, password, output="json")
            data = json.loads(json_data)
            assert len(data.get("folios", [])) == 10
            for folio in data["folios"]:
                for scheme in folio.get("schemes", []):
                    assert scheme["isin"] is not None
                    assert scheme["amfi"] is not None
            assert data.get("investor_info", {}).get("mobile") not in (None, "")
            assert data["cas_type"] == "DETAILED"

    def test_read_summary(self):
        data = self.read_pdf(self.cams_summary_file_name, self.cams_password)
        assert len(data.get("folios", [])) == 4
        for folio in data["folios"]:
            for scheme in folio.get("schemes", []):
                assert scheme["isin"] is not None
                assert scheme["amfi"] is not None
        assert data.get("investor_info", {}).get("mobile") not in (None, "")
        assert data["cas_type"] == "SUMMARY"

    def test_read_dict(self):
        from casparser.cli import cli

        runner = CliRunner()

        for pdf_file, pdf_password in self.pdf_files:
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
