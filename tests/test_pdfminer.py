from click.testing import CliRunner

from .base import BaseTestClass


class TestPDFMiner(BaseTestClass):
    """Test pdfminer parser."""

    @classmethod
    def setup_class(cls):
        BaseTestClass.setup_class()
        cls.mode = "pdfminer"

    def test_cli(self):
        from casparser.cli import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, [self.cams_file_name, "-p", self.cams_password, "--force-pdfminer"]
        )
        assert result.exit_code == 0
        assert "Statement Period:" in result.output
