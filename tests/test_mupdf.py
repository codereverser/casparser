from click.testing import CliRunner
import pytest

from casparser.exceptions import CASParseError
from casparser.enums import FileType
from .base import BaseTestClass


class TestMuPDF(BaseTestClass):
    """Test PyMuPDF parser."""

    def test_cli(self, tmpdir):
        from casparser.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, [self.cams_file_name, "-p", self.cams_password])
        assert result.exit_code == 0
        assert "Statement Period:" in result.output

        fpath = tmpdir.join("output.json")
        result = runner.invoke(
            cli, [self.cams_file_name, "-p", self.cams_password, "-o", fpath.strpath]
        )
        assert result.exit_code == 0
        assert "File saved" in result.output

        fpath = tmpdir.join("output.txt")
        result = runner.invoke(
            cli, [self.cams_file_name, "-p", self.cams_password, "-o", fpath.strpath]
        )
        assert result.exit_code != 1
        assert "Output filename should end" in result.output

        result = runner.invoke(cli, [self.kfintech_file_name, "-p", self.cams_password])
        assert result.exit_code != 0
        assert "Incorrect PDF password!" in result.output

    def test_bad_investor_info(self):
        from casparser.parsers.mupdf import parse_investor_info

        with pytest.raises(CASParseError) as exc_info:
            parse_investor_info({"width": 0, "height": 0, "blocks": []})
        assert "Unable to parse investor data" in str(exc_info)

    def test_bad_file_type(self):
        from casparser.parsers.mupdf import parse_file_type

        file_type = parse_file_type([])
        assert file_type == FileType.UNKNOWN
