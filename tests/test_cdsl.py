"""End-to-end tests for CDSL CAS statements.

Single password-protected fixture (`CDSL_CAS_FILE_1` + `CDSL_CAS_PASSWORD`).
The statement carries a CDSL demat account, an NSDL demat account
(yes, CDSL CAS files can contain NSDL accounts as cross-references),
and a Mutual Fund Folios pseudo-account.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from ._assertions import (
    assert_account_balance_closes,
    assert_demat_account_well_formed,
    assert_equity_well_formed,
    assert_mutual_fund_well_formed,
)

EXPECTED_ACCOUNTS = 3

EXPECTED_PER_ACCOUNT = [
    # (type, equities, mutual_funds, bonds)
    ("CDSL Demat Account", 25, 1, 0),
    ("NSDL Demat Account", 2, 0, 0),
    ("Mutual Fund Folios", 0, 16, 0),
]

PERIOD_FROM = "01-Apr-2025"
PERIOD_TO = "31-Mar-2026"


class TestCDSLStatement:
    def test_file_type_and_period(self, cdsl_data):
        assert cdsl_data.file_type == "CDSL"
        assert cdsl_data.statement_period.from_ == PERIOD_FROM
        assert cdsl_data.statement_period.to == PERIOD_TO

    def test_account_count(self, cdsl_data):
        assert len(cdsl_data.accounts) == EXPECTED_ACCOUNTS

    def test_per_account_holdings_counts(self, cdsl_data):
        for i, (exp_type, n_eq, n_mf, n_bd) in enumerate(EXPECTED_PER_ACCOUNT):
            ac = cdsl_data.accounts[i]
            assert ac.type == exp_type, f"acc {i}: type {ac.type!r}"
            assert len(ac.equities) == n_eq, (
                f"acc {i} ({exp_type}): expected {n_eq} equities, " f"got {len(ac.equities)}"
            )
            assert len(ac.mutual_funds) == n_mf, (
                f"acc {i} ({exp_type}): expected {n_mf} MFs, " f"got {len(ac.mutual_funds)}"
            )
            assert len(ac.bonds) == n_bd

    def test_investor_name_set(self, cdsl_data):
        assert cdsl_data.investor_info.name


class TestCDSLAccountInvariants:
    def test_account_well_formed(self, cdsl_data):
        for ac in cdsl_data.accounts:
            assert_demat_account_well_formed(ac)

    def test_account_balance_closes(self, cdsl_data):
        """Σ(equity.value) + Σ(mf.value) ≈ account.balance."""
        for ac in cdsl_data.accounts:
            assert_account_balance_closes(ac)

    def test_equity_rows_well_formed(self, cdsl_data):
        for ac in cdsl_data.accounts:
            for eq in ac.equities:
                assert_equity_well_formed(eq)

    def test_mutual_fund_rows_well_formed(self, cdsl_data):
        for ac in cdsl_data.accounts:
            for mf in ac.mutual_funds:
                assert_mutual_fund_well_formed(mf)


class TestCDSLOutput:
    """CDSL JSON output preserves the account schema."""

    def test_json_output(self, cdsl_file, cdsl_password):
        from casparser import read_cas_pdf

        raw = read_cas_pdf(cdsl_file, cdsl_password, output="json")
        data = json.loads(raw)
        assert data["file_type"] == "CDSL"
        assert len(data["accounts"]) == EXPECTED_ACCOUNTS
        assert data["investor_info"]["name"]


class TestCDSLCLI:
    def test_cli_renders_table(self, cdsl_file, cdsl_password):
        from casparser.cli import cli
        from tests.conftest import strip_ansi

        result = CliRunner().invoke(
            cli,
            [cdsl_file, "-p", cdsl_password, "-a"],
        )
        assert result.exit_code == 0
        clean = strip_ansi(result.output)
        assert "Statement Period :" in clean
        assert "CDSL" in clean
