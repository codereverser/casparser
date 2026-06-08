"""End-to-end tests for NSDL CAS statements.

Single fixture (`NSDL_CAS_FILE_1`, unencrypted) carrying:

  * 1 NSDL demat account (equities + summary-form bonds)
  * 1 CDSL demat account (equities + detailed MFs + detailed bonds)
  * 1 Mutual Fund Folios pseudo-account

The shape is locked in below (counts only — rupee figures stay out
of the repo). Arithmetic invariants from `_assertions` validate the
parsed numerics without exposing private totals.
"""

from __future__ import annotations

from click.testing import CliRunner

from ._assertions import (
    assert_account_balance_closes,
    assert_bond_detailed_form,
    assert_bond_summary_form,
    assert_demat_account_well_formed,
    assert_equity_well_formed,
    assert_mutual_fund_well_formed,
)

# Exact NSDL fixture shape. Bond counts split into summary-form (NSDL
# demat account) and detailed-form (CDSL demat account).
EXPECTED_ACCOUNTS = 3

EXPECTED_PER_ACCOUNT = [
    # (type, equities, mutual_funds, bonds_summary, bonds_detailed)
    ("NSDL Demat Account", 5, 0, 7, 0),
    ("CDSL Demat Account", 12, 4, 0, 9),
    ("Mutual Fund Folios", 0, 13, 0, 0),
]

PERIOD_FROM = "01-Dec-2020"
PERIOD_TO = "31-Dec-2020"


class TestNSDLStatement:
    """Top-level shape + per-account holdings counts."""

    def test_file_type_and_period(self, nsdl_data):
        # `use_enum_values=True` on the model — file_type is a str.
        assert nsdl_data.file_type == "NSDL"
        assert nsdl_data.statement_period.from_ == PERIOD_FROM
        assert nsdl_data.statement_period.to == PERIOD_TO

    def test_account_count(self, nsdl_data):
        assert len(nsdl_data.accounts) == EXPECTED_ACCOUNTS

    def test_per_account_holdings_counts(self, nsdl_data):
        """Each account's equity / MF / bond count matches the
        fixture exactly. Bond counts are split into summary vs
        detailed form by inspecting the per-bond fields."""
        for i, (exp_type, n_eq, n_mf, n_bd_sum, n_bd_det) in enumerate(EXPECTED_PER_ACCOUNT):
            ac = nsdl_data.accounts[i]
            assert ac.type == exp_type, f"acc {i}: type {ac.type!r}"
            assert len(ac.equities) == n_eq, (
                f"acc {i} ({exp_type}): expected {n_eq} equities, " f"got {len(ac.equities)}"
            )
            assert len(ac.mutual_funds) == n_mf, (
                f"acc {i} ({exp_type}): expected {n_mf} MFs, " f"got {len(ac.mutual_funds)}"
            )
            summary_bonds = sum(1 for b in ac.bonds if b.face_value is not None)
            detailed_bonds = sum(1 for b in ac.bonds if b.market_price is not None)
            assert len(ac.bonds) == n_bd_sum + n_bd_det
            assert (
                summary_bonds == n_bd_sum
            ), f"acc {i}: expected {n_bd_sum} summary-form bonds, got {summary_bonds}"
            assert (
                detailed_bonds == n_bd_det
            ), f"acc {i}: expected {n_bd_det} detailed-form bonds, got {detailed_bonds}"

    def test_investor_name_set(self, nsdl_data):
        # NSDL/CDSL investor extractor populates `name`; mobile/email
        # are not always reliable on NSDL CAS, so we only require name.
        assert nsdl_data.investor_info.name


class TestNSDLAccountInvariants:
    """Arithmetic invariants on each account's holdings."""

    def test_account_well_formed(self, nsdl_data):
        for ac in nsdl_data.accounts:
            assert_demat_account_well_formed(ac)

    def test_account_balance_closes(self, nsdl_data):
        """Σ(equity.value) + Σ(mf.value) + Σ(bond.value) ==
        account.balance for every account. Catches misrouted rows
        and missing-row bugs."""
        for ac in nsdl_data.accounts:
            assert_account_balance_closes(ac)

    def test_equity_rows_well_formed(self, nsdl_data):
        for ac in nsdl_data.accounts:
            for eq in ac.equities:
                assert_equity_well_formed(eq)

    def test_mutual_fund_rows_well_formed(self, nsdl_data):
        """`balance * nav ≈ value` for every MF holding."""
        for ac in nsdl_data.accounts:
            for mf in ac.mutual_funds:
                assert_mutual_fund_well_formed(mf)


class TestNSDLBonds:
    """Per-bond invariants split by source form."""

    def test_summary_form_bonds(self, nsdl_data):
        """Summary-form bonds (NSDL-account page): full metadata,
        `num_bonds * face_value == value` exactly."""
        summary_bonds = [
            b for ac in nsdl_data.accounts for b in ac.bonds if b.face_value is not None
        ]
        assert summary_bonds, "no summary-form bonds found in fixture"
        for bd in summary_bonds:
            assert_bond_summary_form(bd)

    def test_detailed_form_bonds(self, nsdl_data):
        """Detailed-form bonds (CDSL-account page): only quantity +
        market_price + value, `num_bonds * market_price ≈ value`."""
        detailed_bonds = [
            b for ac in nsdl_data.accounts for b in ac.bonds if b.market_price is not None
        ]
        assert detailed_bonds, "no detailed-form bonds found in fixture"
        for bd in detailed_bonds:
            assert_bond_detailed_form(bd)

    def test_every_bond_belongs_to_exactly_one_form(self, nsdl_data):
        """A row is either summary-form (face_value set) or detailed
        (market_price set) — never both, never neither."""
        for ac in nsdl_data.accounts:
            for bd in ac.bonds:
                has_summary = bd.face_value is not None
                has_detailed = bd.market_price is not None
                assert has_summary ^ has_detailed, (
                    f"bond {bd.isin} ambiguous: "
                    f"face_value={bd.face_value} market_price={bd.market_price}"
                )


class TestNSDLCLI:
    def test_cli_renders_table(self, nsdl_file):
        """`casparser <nsdl_file>` renders the rich-table view without
        a password (this fixture is unencrypted)."""
        from casparser.cli import cli
        from tests.conftest import strip_ansi

        result = CliRunner().invoke(cli, [nsdl_file, "-p", "", "-a"])
        assert result.exit_code == 0
        clean = strip_ansi(result.output)
        assert "Statement Period :" in clean
        assert "NSDL" in clean
