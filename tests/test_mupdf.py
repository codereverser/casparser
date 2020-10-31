import os

from click.testing import CliRunner

from .base import BaseTestClass


class TestMuPDF(BaseTestClass):
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
