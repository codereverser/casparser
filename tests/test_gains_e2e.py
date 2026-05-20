"""End-to-end tests for the capital-gains analysis module.

Drives `CapitalGainsReport` and its CSV / 112A exports through a
real parsed KFin DETAILED statement. The fund-level unit tests in
`tests/test_gains.py` cover the building blocks (CII lookup, fund
type detection, MergedTransaction, FIFOUnits internals); these
exercise the report's public API end-to-end so the import-side and
formatting-side paths get hit.
"""

from __future__ import annotations

import os

import pytest

from casparser import read_cas_pdf
from casparser.analysis import CapitalGainsReport
from casparser.exceptions import IncompleteCASError


@pytest.fixture(scope="module")
def kfintech_cas():
    path = os.getenv("KFINTECH_CAS_FILE_NEW")
    pw = os.getenv("KFINTECH_CAS_PASSWORD")
    if not path:
        pytest.skip("KFINTECH_CAS_FILE_NEW not set")
    return read_cas_pdf(path, pw)


def test_capital_gains_report_basic(kfintech_cas):
    """CapitalGainsReport is constructible from a parsed CASData
    and exposes the documented surface."""
    report = CapitalGainsReport(kfintech_cas)
    # `has_gains` / `has_error` are simple guards we want covered.
    assert isinstance(report.has_gains(), bool)
    assert isinstance(report.has_error(), bool)
    # Whether the sample has gains depends on the FY of its
    # transactions, but the FY list should at minimum be a list.
    fy_list = report.get_fy_list()
    assert isinstance(fy_list, list)
    # Sums are decimals, populated even on empty datasets.
    assert report.invested_amount is not None
    assert report.current_value is not None


def test_capital_gains_summary(kfintech_cas):
    """`get_summary` renders the FY → totals breakdown."""
    report = CapitalGainsReport(kfintech_cas)
    summary = report.get_summary()
    # Returns an iterable of rows / strings — just confirm it ran.
    assert summary is not None


def test_capital_gains_csv_outputs(kfintech_cas):
    """The two CSV exports return strings even when there are no
    realised gains in the sample."""
    report = CapitalGainsReport(kfintech_cas)
    summary_csv = report.get_summary_csv_data()
    detailed_csv = report.get_gains_csv_data()
    assert isinstance(summary_csv, str)
    assert isinstance(detailed_csv, str)


def test_capital_gains_112a_report(kfintech_cas):
    """The 112A FY-specific report can be generated even when the FY
    has no entries (returns an empty list)."""
    report = CapitalGainsReport(kfintech_cas)
    fys = report.get_fy_list()
    # Pick any FY with entries; otherwise try a known historical FY
    # — the helper should not crash on an FY with no rows either.
    target_fy = fys[0] if fys else "FY2020-21"
    entries = report.generate_112a(target_fy)
    assert isinstance(entries, list)
    csv_blob = report.generate_112a_csv_data(target_fy)
    assert isinstance(csv_blob, str)


def test_incomplete_cas_raises():
    """Gains analysis on a CAS where any folio has a non-zero opening
    balance should raise IncompleteCASError."""
    cams_path = os.getenv("CAMS_CAS_FILE")
    cams_pw = os.getenv("CAMS_CAS_PASSWORD")
    if not cams_path:
        pytest.skip("CAMS_CAS_FILE not set")
    data = read_cas_pdf(cams_path, cams_pw)
    # CAMS sample's first statement period is mid-stream — opening
    # balances are non-zero, so the report must refuse to compute.
    has_open_bal = any(
        sch.open >= Decimal("0.01") and sch.transactions for f in data.folios for sch in f.schemes
    )
    if not has_open_bal:
        pytest.skip("sample doesn't have non-zero opening balance schemes")
    with pytest.raises(IncompleteCASError):
        CapitalGainsReport(data)


def test_gains_cli(tmp_path):
    """`-g --gains-112a FY2020-21` exercises the full capital-gains
    pipeline through the CLI — the user-facing entry point that
    composes parsing + gains analysis + CSV export."""
    kfin = os.getenv("KFINTECH_CAS_FILE_NEW")
    if not kfin:
        pytest.skip("KFINTECH_CAS_FILE_NEW not set")
    from click.testing import CliRunner

    from casparser.cli import cli

    out = tmp_path / "gains.csv"
    result = CliRunner().invoke(
        cli,
        [
            kfin,
            "-p",
            os.getenv("KFINTECH_CAS_PASSWORD"),
            "-g",
            "--gains-112a",
            "FY2020-21",
            "-o",
            str(out),
        ],
    )
    # 0 = success; 2 = "no gains for that FY" — both acceptable; the
    # goal is exercising the import + analysis paths.
    assert result.exit_code in (0, 2), f"unexpected exit {result.exit_code}: {result.output}"


# Decimal import at file bottom keeps the e2e test module small.
from decimal import Decimal  # noqa: E402
