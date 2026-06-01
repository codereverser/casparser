"""End-to-end tests for KFintech CAS files.

Mirrors the structure of `tests/test_cams.py`: two detailed fixtures
(short-period `KFINTECH_CAS_FILE` + multi-decade `KFINTECH_CAS_FILE_NEW`)
plus a summary fixture (`KFINTECH_CAS_SUMMARY`).
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from casparser.enums import CASFileType

from ._assertions import (
    assert_folio_well_formed,
    assert_investor_info_complete,
    assert_scheme_name_clean,
    assert_scheme_transaction_units_close,
    assert_scheme_valuation_arithmetic,
    assert_scheme_well_formed,
)

# Exact fixture shape (folios, schemes).
DETAILED = {
    "main": dict(folios=17, schemes=30, period_from="01-Jan-1990", period_to="31-Mar-2021"),
    "new": dict(folios=14, schemes=30, period_from="01-Jan-2000", period_to="03-Sep-2023"),
}
SUMMARY = dict(folios=9, schemes=13)


class TestKFinDetailed:
    """Long-history KFintech DETAILED statement (`KFINTECH_CAS_FILE`)."""

    def test_shape(self, kfin_data):
        d = kfin_data
        assert d.file_type == "KFINTECH"
        assert d.cas_type == CASFileType.DETAILED.value
        assert d.statement_period.from_ == DETAILED["main"]["period_from"]
        assert d.statement_period.to == DETAILED["main"]["period_to"]
        assert len(d.folios) == DETAILED["main"]["folios"]
        total_schemes = sum(len(f.schemes) for f in d.folios)
        assert total_schemes == DETAILED["main"]["schemes"]

    def test_investor_info(self, kfin_data):
        assert_investor_info_complete(kfin_data.investor_info)

    def test_every_folio_well_formed(self, kfin_data):
        for folio in kfin_data.folios:
            assert_folio_well_formed(folio)

    def test_every_scheme_well_formed(self, kfin_data):
        for folio in kfin_data.folios:
            for scheme in folio.schemes:
                assert_scheme_well_formed(scheme)

    def test_close_times_nav_equals_valuation(self, kfin_data):
        for folio in kfin_data.folios:
            for scheme in folio.schemes:
                assert_scheme_valuation_arithmetic(scheme)

    def test_open_plus_units_equals_close(self, kfin_data):
        """`scheme.open + Σ(txn.units) == scheme.close` for every
        scheme. Now possible thanks to the same-font subset-cluster
        dedup in extract.py — previously the KFin date overlay
        would corrupt some SIP rows and break this invariant."""
        for folio in kfin_data.folios:
            for scheme in folio.schemes:
                assert_scheme_transaction_units_close(scheme)


class TestKFinDetailedNew:
    """Multi-decade KFintech DETAILED statement (`KFINTECH_CAS_FILE_NEW`)."""

    def test_shape(self, kfin_new_data):
        d = kfin_new_data
        assert d.file_type == "KFINTECH"
        assert d.cas_type == CASFileType.DETAILED.value
        assert d.statement_period.from_ == DETAILED["new"]["period_from"]
        assert d.statement_period.to == DETAILED["new"]["period_to"]
        assert len(d.folios) == DETAILED["new"]["folios"]
        total_schemes = sum(len(f.schemes) for f in d.folios)
        assert total_schemes == DETAILED["new"]["schemes"]

    def test_every_scheme_well_formed(self, kfin_new_data):
        for folio in kfin_new_data.folios:
            for scheme in folio.schemes:
                assert_scheme_well_formed(scheme)

    def test_close_times_nav_equals_valuation(self, kfin_new_data):
        for folio in kfin_new_data.folios:
            for scheme in folio.schemes:
                assert_scheme_valuation_arithmetic(scheme)

    def test_open_plus_units_equals_close(self, kfin_new_data):
        for folio in kfin_new_data.folios:
            for scheme in folio.schemes:
                assert_scheme_transaction_units_close(scheme)


class TestKFinSummary:
    """KFintech SUMMARY statement (`KFINTECH_CAS_SUMMARY`)."""

    def test_shape(self, kfin_summary_data):
        d = kfin_summary_data
        assert d.file_type == "KFINTECH"
        assert d.cas_type == CASFileType.SUMMARY.value
        assert len(d.folios) == SUMMARY["folios"]
        total_schemes = sum(len(f.schemes) for f in d.folios)
        assert total_schemes == SUMMARY["schemes"]

    def test_investor_info(self, kfin_summary_data):
        assert_investor_info_complete(kfin_summary_data.investor_info)

    def test_every_scheme_well_formed(self, kfin_summary_data):
        for folio in kfin_summary_data.folios:
            for scheme in folio.schemes:
                assert_scheme_well_formed(scheme)

    def test_scheme_names_not_footer_bled(self, kfin_summary_data):
        """The last scheme used to swallow the `Total ...` row + the
        trailing disclaimer paragraphs into its name. Guard it."""
        for folio in kfin_summary_data.folios:
            for scheme in folio.schemes:
                assert_scheme_name_clean(scheme)


class TestKFinCLI:
    """One CLI invocation each for the JSON output path and the
    wrong-password error path. Other CLI semantics are covered by
    `test_cams.py`."""

    def test_json_output(self, tmp_path, kfin_file, kfin_password):
        from casparser.cli import cli

        out = tmp_path / "out.json"
        result = CliRunner().invoke(
            cli,
            [kfin_file, "-p", kfin_password, "-o", str(out)],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(out.read_text())
        assert payload["file_type"] == "KFINTECH"

    def test_wrong_password(self, kfin_file, cams_password):
        """Using the CAMS password against a KFin file errors out
        cleanly through the CLI."""
        from casparser.cli import cli

        result = CliRunner().invoke(cli, [kfin_file, "-p", cams_password])
        assert result.exit_code != 0
        assert "Incorrect PDF password!" in result.output
