import json
import re

from click.testing import CliRunner
import pytest

from casparser.exceptions import CASParseError
from casparser.enums import FileType
from .base import BaseTestClass


class TestMuPDF(BaseTestClass):
    """Test PyMuPDF parser."""

    def test_output_json(self):
        json_data = self.read_pdf(self.cams_file_name, self.cams_password, output="json")
        data = json.loads(json_data)
        assert len(data.get("folios", [])) == 10
        assert data["cas_type"] == "DETAILED"

    def test_output_csv(self):
        output = self.read_pdf(self.cams_file_name, self.cams_password, output="csv")
        assert isinstance(output, str)

    def test_cli(self, tmpdir):
        from casparser.cli import cli

        runner = CliRunner()

        fpath = tmpdir.join("output.json")
        result = runner.invoke(
            cli, [self.cams_file_name, "-p", self.cams_password, "-o", fpath.strpath]
        )
        assert result.exit_code == 0
        assert "File saved" in result.output

        fpath = tmpdir.join("output.html")
        result = runner.invoke(
            cli,
            [
                self.cams_summary_file_name,
                "-p",
                self.cams_password,
                "-o",
                fpath.strpath,
                "-s",
                "html",
            ],
        )
        assert result.exit_code != 1
        assert "File saved" in result.output

        fpath = tmpdir.join("output.csv")
        result = runner.invoke(
            cli, [self.cams_file_name, "-p", self.cams_password, "-o", fpath.strpath]
        )
        assert result.exit_code != 1
        assert "File saved" in result.output

        result = runner.invoke(
            cli, [self.kfintech_file_name, "-p", self.kfintech_password, "-s", "html"]
        )
        assert result.exit_code != 1
        assert "<table>\n<thead>" in result.output

        result = runner.invoke(cli, [self.kfintech_file_name, "-p", self.cams_password])
        assert result.exit_code != 0
        assert "Incorrect PDF password!" in result.output

        result = runner.invoke(cli, [self.bad_file_name, "-p", "", "-a"])
        assert result.exit_code == 0
        assert re.search(r"Error\s+:\s+1\s+schemes", result.output) is not None

    def test_bad_investor_info(self):
        from casparser.parsers.mupdf import parse_investor_info

        with pytest.raises(CASParseError) as exc_info:
            parse_investor_info({"width": 0, "height": 0, "blocks": []})
        assert "Unable to parse investor data" in str(exc_info)

    def test_bad_file_type(self):
        from casparser.parsers.mupdf import parse_file_type

        file_type = parse_file_type([])
        assert file_type == FileType.UNKNOWN
