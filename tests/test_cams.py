"""End-to-end tests for CAMS CAS files.

Two detailed fixtures (`CAMS_CAS_FILE` short-period + `CAMS_CAS_FILE_NEW`
multi-decade) plus a summary fixture (`CAMS_CAS_SUMMARY`). Each test
parses one fixture via a module-scoped fixture in `conftest.py`.

Assertions cover:
  * Exact folio + scheme counts (regression guard for schema-detection)
  * Schema-level invariants: ISIN/AMFI/RTA populated, PAN well-formed,
    valuation.nav > 0
  * Arithmetic invariant `close * valuation.nav ≈ valuation.value`
    (catches column-swap and decimal-parse bugs without encoding
    rupee figures from the private fixture)
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from casparser.enums import CASFileType

from ._assertions import (
    assert_folio_well_formed,
    assert_investor_info_complete,
    assert_scheme_transaction_units_close,
    assert_scheme_valuation_arithmetic,
    assert_scheme_well_formed,
)

# Exact fixture shape (folios, schemes) — locked in to catch any
# regression in header / footer / table-boundary detection.
DETAILED = {
    "main": dict(folios=10, schemes=14, period_from="01-Apr-2018", period_to="30-Jun-2018"),
    "new": dict(folios=14, schemes=30, period_from="01-Jan-2000", period_to="31-Aug-2023"),
}
SUMMARY = dict(folios=4, schemes=6)


# --- detailed -------------------------------------------------------------


class TestCAMSDetailed:
    """Short-period CAMS DETAILED statement (`CAMS_CAS_FILE`)."""

    def test_shape(self, cams_data):
        d = cams_data
        assert d.file_type == "CAMS"
        assert d.cas_type == CASFileType.DETAILED.value
        assert d.statement_period.from_ == DETAILED["main"]["period_from"]
        assert d.statement_period.to == DETAILED["main"]["period_to"]
        assert len(d.folios) == DETAILED["main"]["folios"]
        total_schemes = sum(len(f.schemes) for f in d.folios)
        assert total_schemes == DETAILED["main"]["schemes"]

    def test_investor_info(self, cams_data):
        assert_investor_info_complete(cams_data.investor_info)

    def test_every_folio_well_formed(self, cams_data):
        for folio in cams_data.folios:
            assert_folio_well_formed(folio)

    def test_every_scheme_well_formed(self, cams_data):
        for folio in cams_data.folios:
            for scheme in folio.schemes:
                assert_scheme_well_formed(scheme)

    def test_close_times_nav_equals_valuation(self, cams_data):
        """`scheme.close * scheme.valuation.nav` reproduces
        `scheme.valuation.value` for every scheme."""
        for folio in cams_data.folios:
            for scheme in folio.schemes:
                assert_scheme_valuation_arithmetic(scheme)

    def test_open_plus_units_equals_close(self, cams_data):
        """`scheme.open + Σ(txn.units) == scheme.close` exactly for
        every scheme. Catches dropped / mis-dated / duplicated
        transactions on the unit side."""
        for folio in cams_data.folios:
            for scheme in folio.schemes:
                assert_scheme_transaction_units_close(scheme)

    def test_json_output(self, cams_file, cams_password):
        """JSON serialization round-trip preserves the schema."""
        from casparser import read_cas_pdf

        raw = read_cas_pdf(cams_file, cams_password, output="json")
        data = json.loads(raw)
        assert data["file_type"] == "CAMS"
        assert data["cas_type"] == CASFileType.DETAILED.value
        assert len(data["folios"]) == DETAILED["main"]["folios"]
        # Every scheme keeps its ISIN/AMFI through JSON serialization.
        for f in data["folios"]:
            for s in f["schemes"]:
                assert s["isin"], f"JSON: scheme without ISIN: {s['scheme']!r}"
                assert s["amfi"], f"JSON: scheme without AMFI: {s['scheme']!r}"


class TestCAMSDetailedNew:
    """Multi-decade CAMS DETAILED statement (`CAMS_CAS_FILE_NEW`)."""

    def test_shape(self, cams_new_data):
        d = cams_new_data
        assert d.file_type == "CAMS"
        assert d.cas_type == CASFileType.DETAILED.value
        assert d.statement_period.from_ == DETAILED["new"]["period_from"]
        assert d.statement_period.to == DETAILED["new"]["period_to"]
        assert len(d.folios) == DETAILED["new"]["folios"]
        total_schemes = sum(len(f.schemes) for f in d.folios)
        assert total_schemes == DETAILED["new"]["schemes"]

    def test_every_scheme_well_formed(self, cams_new_data):
        for folio in cams_new_data.folios:
            for scheme in folio.schemes:
                assert_scheme_well_formed(scheme)

    def test_close_times_nav_equals_valuation(self, cams_new_data):
        for folio in cams_new_data.folios:
            for scheme in folio.schemes:
                assert_scheme_valuation_arithmetic(scheme)

    def test_open_plus_units_equals_close(self, cams_new_data):
        for folio in cams_new_data.folios:
            for scheme in folio.schemes:
                assert_scheme_transaction_units_close(scheme)


# --- summary --------------------------------------------------------------


class TestCAMSSummary:
    """CAMS SUMMARY statement (`CAMS_CAS_SUMMARY`)."""

    def test_shape(self, cams_summary_data):
        d = cams_summary_data
        assert d.file_type == "CAMS"
        assert d.cas_type == CASFileType.SUMMARY.value
        assert len(d.folios) == SUMMARY["folios"]
        total_schemes = sum(len(f.schemes) for f in d.folios)
        assert total_schemes == SUMMARY["schemes"]

    def test_investor_info(self, cams_summary_data):
        assert_investor_info_complete(cams_summary_data.investor_info)

    def test_every_scheme_well_formed(self, cams_summary_data):
        for folio in cams_summary_data.folios:
            for scheme in folio.schemes:
                assert_scheme_well_formed(scheme)

    def test_close_times_nav_equals_valuation(self, cams_summary_data):
        for folio in cams_summary_data.folios:
            for scheme in folio.schemes:
                assert_scheme_valuation_arithmetic(scheme)


# --- CLI ------------------------------------------------------------------


class TestCAMSCLI:
    """One CLI invocation per output format proves the wiring still works.

    Detailed assertion of CLI semantics for other issuers is covered
    in their own files; this file owns the CAMS-specific paths
    (default JSON, CSV detailed, summary CSV, `-s` summary table)."""

    def test_default_invocation(self, tmp_path, cams_file, cams_password):
        from casparser.cli import cli

        out = tmp_path / "out.json"
        result = CliRunner().invoke(
            cli,
            [cams_file, "-p", cams_password, "-o", str(out)],
        )
        assert result.exit_code == 0, result.output
        assert "File saved" in result.output
        payload = json.loads(out.read_text())
        assert payload["file_type"] == "CAMS"

    def test_csv_output(self, tmp_path, cams_file, cams_password):
        from casparser.cli import cli

        out = tmp_path / "out.csv"
        result = CliRunner().invoke(
            cli,
            [cams_file, "-p", cams_password, "-o", str(out)],
        )
        assert result.exit_code == 0, result.output
        assert "File saved" in result.output
        # cas2csv columns
        content = out.read_text()
        for col in ("amc", "folio", "isin", "amfi", "scheme"):
            assert col in content, f"missing CSV column {col!r}"

    def test_summary_csv_output(self, tmp_path, cams_file, cams_password):
        """`-s -o file.csv` writes the SUMMARY-format CSV (covers
        `cas2csv_summary`)."""
        from casparser.cli import cli

        out = tmp_path / "summary.csv"
        result = CliRunner().invoke(
            cli,
            [cams_file, "-p", cams_password, "-s", "-o", str(out)],
        )
        assert result.exit_code == 0
        content = out.read_text()
        for col in ("amc", "folio", "isin", "amfi", "scheme"):
            assert col in content, f"missing summary CSV column {col!r}"

    def test_summary_terminal_output(self, cams_file, cams_password):
        """CLI without `-o` renders a rich table to the terminal."""
        from casparser.cli import cli
        from tests.conftest import strip_ansi

        result = CliRunner().invoke(cli, [cams_file, "-p", cams_password, "-a"])
        assert result.exit_code == 0
        clean = strip_ansi(result.output)
        assert "Statement Period :" in clean
