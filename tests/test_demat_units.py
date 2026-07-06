"""Unit tests for NSDL/CDSL parser helpers — exercise the corner-case
branches that the end-to-end NSDL+CDSL fixtures don't hit (decimal
parsing edge cases, joint-name owner block, summary-table row
recognisers, MF holdings row anomaly handling, etc.)."""

from __future__ import annotations

from decimal import Decimal

import pytest

import casparser.parsers.cdsl as cdsl_p
import casparser.parsers.nsdl as nsdl_p
from casparser.parsers.pageobj import (
    SOFT_HYPHEN,
    Atom,
    Block,
    Cell,
    _cells_from_block_atoms,
    _join_column_atoms,
)


def _cell(
    text: str, x_left: float = 0.0, x_right: float = 10.0, y_top: float = 0.0, y_bot: float = 0.0
) -> Cell:
    """Construct a `Cell` with one synthetic atom backing it."""
    a = Atom(x_left, x_right, y_top, y_bot, text, "Helvetica", stream_seq=0)
    return Cell(
        x_left=x_left,
        x_right=x_right,
        y_top=y_top,
        y_bot=y_bot,
        text=text,
        atoms=[a],
    )


def _block(*cells: Cell, page: int = 8) -> Block:
    return Block(page=page, cells=list(cells))


# ---------------------------------------------------------------- decimals


class TestDecimalHelpers:
    """Exercises NSDL + CDSL `_to_decimal` / `_opt_decimal` edge cases.
    Both modules carry a copy of the helpers; we test both to make sure
    the branches in each file are hit."""

    @pytest.mark.parametrize("mod", [nsdl_p, cdsl_p])
    def test_to_decimal_handles_none(self, mod):
        assert mod._to_decimal(None) == Decimal(0)

    @pytest.mark.parametrize("mod", [nsdl_p, cdsl_p])
    @pytest.mark.parametrize("placeholder", ["", " ", "-", "--", "N.A", "NA"])
    def test_to_decimal_handles_placeholders(self, mod, placeholder):
        assert mod._to_decimal(placeholder) == Decimal(0)

    @pytest.mark.parametrize("mod", [nsdl_p, cdsl_p])
    def test_to_decimal_strips_commas(self, mod):
        assert mod._to_decimal("1,23,456.78") == Decimal("123456.78")

    @pytest.mark.parametrize("mod", [nsdl_p, cdsl_p])
    def test_to_decimal_swallows_invalid(self, mod):
        # An unparseable string falls back to 0 rather than raising.
        assert mod._to_decimal("not a number") == Decimal(0)

    @pytest.mark.parametrize("mod", [nsdl_p, cdsl_p])
    def test_opt_decimal_returns_none_on_placeholders(self, mod):
        assert mod._opt_decimal(None) is None
        assert mod._opt_decimal("--") is None
        assert mod._opt_decimal("") is None
        assert mod._opt_decimal("garbage!") is None

    @pytest.mark.parametrize("mod", [nsdl_p, cdsl_p])
    def test_opt_decimal_parses_value(self, mod):
        assert mod._opt_decimal("1,234.5") == Decimal("1234.5")


# ---------------------------------------------------------------- CDSL


class TestCDSLHelpers:
    def test_split_bo_id_cdsl(self):
        # All-digit BO ID → CDSL; first 8 = DP, last 8 = client.
        assert cdsl_p._split_bo_id("1111222233334444") == (
            "CDSL",
            "11112222",
            "33334444",
        )

    def test_split_bo_id_nsdl(self):
        # NSDL DP IDs start with `IN`.
        assert cdsl_p._split_bo_id("IN12345699998888") == (
            "NSDL",
            "IN123456",
            "99998888",
        )

    def test_split_bo_id_invalid_length(self):
        assert cdsl_p._split_bo_id("12345") == ("", "", "")

    def test_split_bo_id_unrecognised(self):
        # Doesn't start with IN and isn't all digits → can't classify.
        assert cdsl_p._split_bo_id("ABCD123412341234") == ("", "", "")

    def test_account_key_normalises(self):
        assert cdsl_p._account_key("cdsl", " 11112222 ", "33334444 ") == (
            "CDSL",
            "11112222",
            "33334444",
        )

    def test_full_type_format(self):
        assert cdsl_p._full_type("cdsl") == "CDSL Demat Account"

    def test_looks_numeric(self):
        assert cdsl_p._looks_numeric("1,234.5")
        assert cdsl_p._looks_numeric("-100")
        assert not cdsl_p._looks_numeric("ARN-0005")
        assert not cdsl_p._looks_numeric("DIRECT")
        assert not cdsl_p._looks_numeric("")

    def test_looks_numeric_leading_dot(self):
        """CDSL drops the leading zero on small fractional balances
        (`0.196` -> `.196`); the classifier must still treat them as
        numeric, otherwise the cell mis-buckets and the row layout
        shifts silently."""
        assert cdsl_p._looks_numeric(".196")
        assert cdsl_p._looks_numeric(".69")
        assert cdsl_p._looks_numeric("-.5")
        assert cdsl_p._looks_numeric("0.196")
        # naked separators must still fail
        assert not cdsl_p._looks_numeric(".")
        assert not cdsl_p._looks_numeric("-")

    def test_is_total_row(self):
        block = _block(_cell("Sub Total"), _cell("100.00"))
        assert cdsl_p._is_total_row(block)
        block2 = _block(_cell("INE000A01001"), _cell("100"))
        assert not cdsl_p._is_total_row(block2)

    def test_is_holdings_header(self):
        # A column-header row mentioning ISIN + Security keywords but
        # NOT carrying an actual ISIN value.
        block = _block(
            _cell("ISIN"),
            _cell("Security"),
            _cell("Current Bal"),
            _cell("Market Price"),
        )
        assert cdsl_p._is_holdings_header(block)
        # A data row IS NOT a header.
        data_row = _block(_cell("INE000A01001"), _cell("EXAMPLE COMPANY LIMITED"))
        assert not cdsl_p._is_holdings_header(data_row)

    def test_parse_holdings_row_rejects_no_isin(self):
        block = _block(_cell("Not an ISIN"), _cell("name"), _cell("100"))
        assert cdsl_p._parse_holdings_row(block) is None

    def test_parse_holdings_row_skips_at_marker(self):
        """The leading `@` marker (suspended issue) sits between ISIN
        and name and should be skipped."""
        block = _block(
            _cell("INE000A01001", 20, 60),
            _cell("@", 80, 85),
            _cell("EXAMPLE COMPANY LIMITED", 90, 200),
            _cell("100", 240, 270),
            _cell("--", 300, 320),
            _cell("--", 340, 360),
            _cell("--", 380, 400),
            _cell("100", 440, 460),
            _cell("450.50", 500, 540),
            _cell("45050.00", 560, 620),
        )
        row = cdsl_p._parse_holdings_row(block)
        assert row is not None
        isin, name, shares, price, value = row
        assert isin == "INE000A01001"
        assert name == "EXAMPLE COMPANY LIMITED"
        assert shares == Decimal("100")
        assert price == Decimal("450.50")
        assert value == Decimal("45050.00")

    def test_parse_holdings_row_all_dashes(self):
        """Rights-entitlement rows with all-`--` balances should still
        parse — the `data_start` finder accepts `--` as the first data
        cell."""
        block = _block(
            _cell("INE000A01002", 20, 60),
            _cell("EXAMPLE RIGHTS ENTITL", 80, 200),
            _cell("--", 240, 260),
            _cell("--", 300, 320),
            _cell("--", 340, 360),
            _cell("--", 380, 400),
            _cell("--", 440, 460),
            _cell("6.29", 500, 540),
            _cell("0.00", 560, 620),
        )
        row = cdsl_p._parse_holdings_row(block)
        assert row is not None
        _, _, shares, price, value = row
        assert shares == Decimal(0)
        assert price == Decimal("6.29")
        assert value == Decimal(0)

    def test_parse_holdings_row_returns_none_on_short_block(self):
        block = _block(_cell("INE000A01001"), _cell("name"), _cell("100"))
        # Only 3 cells — fewer than the 3 trailing data cells required.
        assert cdsl_p._parse_holdings_row(block) is None

    def test_mf_holdings_full_row_with_invested_and_value(self):
        """13-cell template (distribution mode + invested + value):
        units | NAV | invested | value get assigned positionally."""
        block = _block(
            _cell("EXFND - Example Fund", 22, 90),
            _cell("INF000A01001", 192, 230),
            _cell("12345", 273, 300),
            _cell("ARN-1234", 320, 360),  # distribution mode (non-numeric)
            _cell("100.000", 380, 410),  # units
            _cell("25.0000", 430, 460),  # NAV
            _cell("2000.00", 480, 510),  # invested
            _cell("2500.00", 530, 560),  # value
        )
        mf = cdsl_p._parse_mf_holdings_row(block, {})
        assert mf is not None
        assert mf.balance == Decimal("100.000")
        assert mf.nav == Decimal("25.0000")
        assert mf.total_cost == Decimal("2000.00")
        assert mf.value == Decimal("2500.00")

    def test_mf_holdings_reduced_row_distrib_no_invested(self):
        """Reduced template: distribution-mode column present but NO
        separate 'invested' column (units | NAV | value). The third
        numeric is the current value, not the cost — regression for a
        row that previously parsed value=0."""
        block = _block(
            _cell("EXFND - Example Fund", 22, 90),
            _cell("INF000A01001", 192, 230),
            _cell("12345", 273, 300),
            _cell("DIR", 320, 360),  # distribution mode (non-numeric)
            _cell("100.000", 380, 410),  # units
            _cell("25.0000", 430, 460),  # NAV
            _cell("2500.00", 530, 560),  # value (no invested column)
        )
        mf = cdsl_p._parse_mf_holdings_row(block, {})
        assert mf is not None
        assert mf.balance == Decimal("100.000")
        assert mf.nav == Decimal("25.0000")
        assert mf.value == Decimal("2500.00")  # not 0
        assert mf.total_cost is None

    def test_mf_holdings_wrapped_folio(self):
        """A long folio wraps its `<digits>/<digits>` tail into the next
        cell ("910121125" | "82/0"). It must be spliced back into the
        full folio (91012112582/0), not truncated to its head — and the
        tail must not be mistaken for the distribution-mode column, which
        would shift the numerics. Regression for dropped folio tails."""
        block = _block(
            _cell("SPGD - Motilal Oswal S&P 500 Index Fund", 22, 120),
            _cell("INF247L01AG2", 192, 230),
            _cell("910121125", 273, 300),  # folio head
            _cell("82/0", 305, 325),  # folio tail (wrapped onto next cell)
            _cell("DIRECT", 340, 380),  # distribution mode
            _cell("20037.345", 400, 430),  # units
            _cell("28.3293", 450, 480),  # NAV
            _cell("250504.20", 500, 530),  # invested
            _cell("567643.96", 550, 580),  # value
        )
        mf = cdsl_p._parse_mf_holdings_row(block, {})
        assert mf is not None
        # Full folio reconstructed (no dash, matching the authoritative
        # "Folio No :" block), not truncated to "910121125".
        assert mf.folio == "91012112582/0"
        # Numerics still align after the tail cell is consumed by folio.
        assert mf.balance == Decimal("20037.345")
        assert mf.nav == Decimal("28.3293")
        assert mf.total_cost == Decimal("250504.20")
        assert mf.value == Decimal("567643.96")
        # value is consistent with balance * nav
        assert abs(mf.balance * mf.nav - mf.value) <= Decimal("0.01")

    def test_mf_holdings_pnl_identity_full_row(self):
        """Full distrib row: profit and return% at the tail."""
        block = _block(
            _cell("EXFND - Example Fund", 22, 90),
            _cell("INF000A01001", 192, 230),
            _cell("12345", 273, 300),
            _cell("DIRECT", 320, 360),
            _cell("100.000", 380, 410),
            _cell("25.0000", 430, 460),
            _cell("2000.00", 480, 510),
            _cell("2500.00", 530, 560),
            _cell("0.10", 570, 590),
            _cell("0", 600, 620),
            _cell("500.00", 630, 650),
            _cell("25.00", 660, 680),
        )
        mf = cdsl_p._parse_mf_holdings_row(block, {})
        assert mf is not None
        assert mf.pnl == Decimal("500.00")
        assert mf.return_ == Decimal("25.00")

    def test_mf_holdings_return_only_tail(self):
        """1684130326 geometry: no printed profit; return% must not land in pnl."""
        block = _block(
            _cell("32Z - Aditya Birla Sun Life Corporate Bond Fund - ", 21.5, 90),
            _cell("INF209K01S38", 112.3, 230),
            _cell("1040936382", 167.2, 300),
            _cell("DIRECT", 219.0, 360),
            _cell("ARN\x02", 222.8, 380),
            _cell("11.343", 269.4, 410),
            _cell("95.6053", 299.6, 460),
            _cell("1,000.00", 348.1, 510),
            _cell("1,084.45", 398.1, 560),
            _cell("0", 469.1, 590),
            _cell(".31", 507.9, 620),
            _cell("0", 574.1, 650),
        )
        mf = cdsl_p._parse_mf_holdings_row(block, {})
        assert mf is not None
        assert mf.value == Decimal("1084.45")
        assert mf.total_cost == Decimal("1000.00")
        assert mf.pnl is None
        assert mf.return_ == Decimal("0.31")


# ---------------------------------------------------------------- NSDL


class TestNSDLHelpers:
    def test_full_type_format(self):
        assert nsdl_p._full_type("cdsl") == "CDSL Demat Account"
        assert nsdl_p._full_type("nsdl") == "NSDL Demat Account"

    def test_account_key_normalises(self):
        assert nsdl_p._account_key("nsdl", " IN301151 ", " 12241815 ") == (
            "NSDL",
            "IN301151",
            "12241815",
        )

    def test_is_total_row(self):
        assert nsdl_p._is_total_row(_block(_cell("Sub Total"), _cell("100")))
        assert nsdl_p._is_total_row(_block(_cell("Grand Total"), _cell("1,00,000")))
        assert not nsdl_p._is_total_row(_block(_cell("INE000A01001")))

    def test_section_marker_kind(self):
        assert nsdl_p._section_marker_kind(_block(_cell("Equity Shares"))) == "equities"
        assert nsdl_p._section_marker_kind(_block(_cell("Mutual Funds (M)"))) == "mfunds"
        assert nsdl_p._section_marker_kind(_block(_cell("Corporate Bonds (C)"))) == "bonds"
        # Unsupported markers are still recognised so we don't misroute
        # the next data row into the previous section.
        assert nsdl_p._section_marker_kind(_block(_cell("Preference Shares (P)"))) == "unsupported"
        # A multi-cell row is not a marker.
        assert (
            nsdl_p._section_marker_kind(_block(_cell("Equity Shares"), _cell("A"), _cell("B")))
            is None
        )
        # An unknown short label is not a marker.
        assert nsdl_p._section_marker_kind(_block(_cell("Random Caption"))) is None

    def test_detect_mode_from_header(self):
        # MF Holdings table
        mfh = _block(
            _cell("ISIN"),
            _cell("ISIN Description"),
            _cell("Folio No."),
            _cell("No. of Units"),
            _cell("Average"),
            _cell("Total Cost"),
        )
        assert nsdl_p._detect_mode_from_header(mfh) == "mf_holdings"
        # Detailed equity table without a section hint -> equities_detailed.
        eq_det = _block(
            _cell("ISIN"),
            _cell("Security"),
            _cell("Current Bal"),
            _cell("Market Price"),
            _cell("Value in"),
        )
        assert nsdl_p._detect_mode_from_header(eq_det) == "equities_detailed"
        # ... but the same header in a 'bonds' context routes to bonds_detailed,
        # and in an 'mfunds' context to mfunds_detailed.
        assert nsdl_p._detect_mode_from_header(eq_det, "bonds") == "bonds_detailed"
        assert nsdl_p._detect_mode_from_header(eq_det, "mfunds") == "mfunds_detailed"
        # Summary bonds table.
        bd_sum = _block(
            _cell("ISIN"),
            _cell("Company Name"),
            _cell("Coupon Rate"),
            _cell("Frequency"),
            _cell("Maturity Date"),
            _cell("Face Value"),
        )
        assert nsdl_p._detect_mode_from_header(bd_sum) == "bonds_summary"
        # Summary equity table
        eq_sum = _block(
            _cell("Stock Symbol"),
            _cell("ISIN"),
            _cell("Company Name"),
        )
        assert nsdl_p._detect_mode_from_header(eq_sum) == "equities_summary"
        # Summary MF table
        mf_sum = _block(
            _cell("ISIN"),
            _cell("ISIN Description"),
            _cell("NAV"),
        )
        assert nsdl_p._detect_mode_from_header(mf_sum) == "mfunds_summary"
        # A data row (carrying a real ISIN) is NOT a header.
        data_row = _block(_cell("INE000A01001"), _cell("Some Stock"))
        assert nsdl_p._detect_mode_from_header(data_row) is None
        # A truly unrecognised row returns None.
        unknown = _block(_cell("Foo"), _cell("Bar"))
        assert nsdl_p._detect_mode_from_header(unknown) is None

    def test_is_table_header(self):
        # Multiple header keywords + no ISIN → header.
        hdr = _block(
            _cell(
                "ISIN Description    No. of\nUnits    Stock Symbol    " "Market Price    Value in"
            )
        )
        assert nsdl_p._is_table_header(hdr)
        # Carrying an ISIN → data row, not header.
        data_row = _block(_cell("INE000A01001 some stock"))
        assert not nsdl_p._is_table_header(data_row)

    def test_parse_equity_row_summary_format(self):
        """Summary equity row: ISIN, name, face_value, num_shares,
        price, value. We take the last three numerics."""
        block = _block(
            _cell("INE000A01001\nEXAMPLECO.NSE"),
            _cell("EXAMPLE COMPANY LIMITED"),
            _cell("1.00"),  # face value
            _cell("100"),  # num_shares
            _cell("450.50"),  # price
            _cell("45,050.00"),  # value
        )
        eq = nsdl_p._parse_equity_row(block, detailed=False)
        assert eq is not None
        assert eq.isin == "INE000A01001"
        assert eq.num_shares == Decimal("100")
        assert eq.price == Decimal("450.50")
        assert eq.value == Decimal("45050.00")

    def test_parse_equity_row_detailed_format(self):
        """Detailed equity row: 11 numerics; num_shares = first."""
        block = _block(
            _cell("INE000A01001"),
            _cell("EXAMPLE COMPANY LIMITED"),
            _cell("100"),  # current_bal = num_shares
            _cell("100"),  # free_bal
            _cell("0"),
            _cell("0"),
            _cell("0"),
            _cell("0"),
            _cell("0"),
            _cell("0"),
            _cell("0"),
            _cell("450.50"),  # market_price
            _cell("45,050.00"),  # value
        )
        eq = nsdl_p._parse_equity_row(block, detailed=True)
        assert eq is not None
        assert eq.num_shares == Decimal("100")
        assert eq.price == Decimal("450.50")
        assert eq.value == Decimal("45050.00")

    def test_parse_equity_row_pledged_picks_closing_shares(self):
        """Pledged equity rows print a sub-amount and duplicate share counts
        before price/value — pick the count that closes shares*price~=value."""
        block = _block(
            _cell("INE552Z01027\nABDL.NSE"),
            _cell("ALLIED BLENDERS AND DISTILLERS LIMITED"),
            _cell("2.00"),  # pledged sub-amount
            _cell("300"),
            _cell("300"),
            _cell("558.30"),
            _cell("1,67,490.00"),
        )
        eq = nsdl_p._parse_equity_row(block, detailed=False)
        assert eq is not None
        assert eq.num_shares == Decimal("300")
        assert eq.price == Decimal("558.30")
        assert eq.value == Decimal("167490.00")

    def test_parse_equity_row_rejects_no_isin(self):
        block = _block(_cell("not-an-isin"), _cell("name"), _cell("1"), _cell("2"), _cell("3"))
        assert nsdl_p._parse_equity_row(block) is None

    def test_parse_equity_row_rejects_too_few_numerics(self):
        block = _block(_cell("INE000A01001"), _cell("name"), _cell("1"), _cell("2"))
        assert nsdl_p._parse_equity_row(block) is None

    def test_parse_summary_mf_row(self):
        block = _block(
            _cell("INF000A01002"),
            _cell("NIPPON INDIA ETF LIQUID BeES"),
            _cell("100.001"),  # units
            _cell("1000.00"),  # NAV
            _cell("100,000.00"),  # value
        )
        mf = nsdl_p._parse_summary_mf_row(block)
        assert mf is not None
        assert mf.isin == "INF000A01002"
        assert mf.balance == Decimal("100.001")
        assert mf.value == Decimal("100000.00")

    def test_parse_summary_mf_row_rejects_non_isin(self):
        block = _block(_cell("not-an-isin"), _cell("name"))
        assert nsdl_p._parse_summary_mf_row(block) is None

    def test_parse_summary_mf_row_pledged_picks_closing_balance(self):
        """A pledged holding prints two unit numerics — the total balance
        and the 'of which pledged' sub-amount — before NAV and value, in no
        fixed order. The parser must pick the balance that closes
        ``balance * nav ~= value``, not the leading numeric. Mirrors the
        AXIS MULTICAP pledged row that used to read balance=5,628 (the
        pledged sub-amount) instead of 7,589.734 (the total)."""
        block = _block(
            _cell("INF846K016E3"),
            _cell("AXIS MULTICAP FUND-REGULAR PLAN GROWTH"),
            _cell("5,628.000"),  # 'of which pledged' sub-amount (leads)
            _cell("7,589.734"),  # actual total balance
            _cell("18.20"),  # NAV
            _cell("1,38,133.15"),  # value (7,589.734 * 18.20)
        )
        mf = nsdl_p._parse_summary_mf_row(block)
        assert mf is not None
        assert mf.balance == Decimal("7589.734")
        assert mf.nav == Decimal("18.20")
        assert mf.value == Decimal("138133.15")

    def test_pick_balance_closing(self):
        nav, value = Decimal("18.20"), Decimal("138133.15")
        # Picks the candidate that closes balance * nav ~= value.
        assert nsdl_p._pick_balance_closing(
            [Decimal("5628.000"), Decimal("7589.734")], nav, value
        ) == Decimal("7589.734")
        # Single candidate is returned as-is.
        assert nsdl_p._pick_balance_closing([Decimal("100.001")], nav, value) == Decimal("100.001")
        # No candidate closes -> largest positive fallback.
        assert nsdl_p._pick_balance_closing(
            [Decimal("10"), Decimal("20")], Decimal("0"), Decimal("0")
        ) == Decimal("20")
        # Empty -> zero.
        assert nsdl_p._pick_balance_closing([], nav, value) == Decimal(0)

    def test_parse_mf_holdings_row_with_misplaced_ucc(self):
        """The NSDL MF Holdings table sometimes renders the UCC as a
        lone digit (`8`) at the units column's x-position. The parser
        should fold that into the UCC field rather than the numerics."""
        block = _block(
            _cell("INF000A01003\nNOT AVAILABLE", 20.0, 75.0),
            _cell("ICICI Prudential\nCorporate Bond", 80.0, 145.0),
            _cell("26777337", 167.0, 198.0),
            _cell("89,935.20", 204.0, 235.0),
            _cell("8", 231.9, 235.2),  # misplaced UCC
            _cell("27.7978", 280.0, 305.0),
            _cell("25,00,000.00", 320.0, 360.0),
            _cell("29.3146", 393.0, 418.0),
            _cell("26,36,414.65", 433.0, 473.0),
            _cell("1,36,414.65", 486.0, 522.0),
            _cell("8.61", 561.0, 574.0),
        )
        mf = nsdl_p._parse_mf_holdings_row(block)
        assert mf is not None
        assert mf.isin == "INF000A01003"
        assert mf.folio == "26777337"
        assert mf.balance == Decimal("89935.20")
        assert mf.ucc == "8"
        assert mf.nav == Decimal("29.3146")
        assert mf.value == Decimal("2636414.65")
        assert mf.pnl == Decimal("136414.65")
        assert mf.return_ == Decimal("8.61")

    def test_parse_mf_holdings_row_right_shifted_layout(self):
        """Same row as the misplaced-UCC fixture, shifted ~33px right
        (nav≈426, value≈484, pnl≈555) — the trigger-statement geometry."""
        block = _block(
            _cell("INF000A01003\nNOT AVAILABLE", 20.0, 75.0),
            _cell("ICICI Prudential\nCorporate Bond", 80.0, 145.0),
            _cell("26777337", 167.0, 198.0),
            _cell("89,935.20", 204.0, 235.0),
            _cell("8", 231.9, 235.2),
            _cell("27.7978", 313.0, 338.0),
            _cell("25,00,000.00", 353.0, 393.0),
            _cell("29.3146", 426.0, 451.0),
            _cell("26,36,414.65", 484.0, 524.0),
            _cell("1,36,414.65", 555.0, 591.0),
            _cell("8.61", 630.0, 643.0),
        )
        mf = nsdl_p._parse_mf_holdings_row(block)
        assert mf is not None
        assert mf.nav == Decimal("29.3146")
        assert mf.value == Decimal("2636414.65")
        assert mf.pnl == Decimal("136414.65")
        assert mf.return_ == Decimal("8.61")

    def test_parse_mf_holdings_row_reduced_columns(self):
        """Row with no avg/total cost — only units, nav, value."""
        block = _block(
            _cell("INF000A01004", 20.0, 75.0),
            _cell("Liquid Fund", 80.0, 145.0),
            _cell("12345678", 167.0, 198.0),
            _cell("100.001", 204.0, 235.0),
            _cell("1000.00", 393.0, 418.0),
            _cell("100,000.00", 433.0, 473.0),
        )
        mf = nsdl_p._parse_mf_holdings_row(block)
        assert mf is not None
        assert mf.balance == Decimal("100.001")
        assert mf.nav == Decimal("1000.00")
        assert mf.value == Decimal("100000.00")
        assert mf.avg_cost is None
        assert mf.total_cost is None
        assert mf.pnl is None
        assert mf.return_ is None

    def test_parse_mf_holdings_row_pnl_without_returns(self):
        """Live INF2JJD01169 shape: tail ends with pnl only, no returns %."""
        block = _block(
            _cell("INF2JJD01169", 20.0, 75.0),
            _cell("Some Scheme", 80.0, 145.0),
            _cell("6653121493", 167.0, 198.0),
            _cell("194.410", 204.0, 235.0),
            _cell("10.2875", 280.0, 305.0),
            _cell("2000", 320.0, 360.0),
            _cell("10.2280", 393.0, 418.0),
            _cell("1988.43", 433.0, 473.0),
            _cell("-11.57", 486.0, 522.0),
        )
        mf = nsdl_p._parse_mf_holdings_row(block)
        assert mf is not None
        assert mf.nav == Decimal("10.2280")
        assert mf.value == Decimal("1988.43")
        assert mf.total_cost == Decimal("2000")
        assert mf.pnl == Decimal("-11.57")
        assert mf.return_ is None

    def test_parse_mf_holdings_row_near_par_value(self):
        """Near-par market value (0.58% below cost) must not be dropped."""
        block = _block(
            _cell("INF2JJD01169", 20.0, 75.0),
            _cell("Near Par Fund", 80.0, 145.0),
            _cell("6653121493", 167.0, 198.0),
            _cell("194.410", 204.0, 235.0),
            _cell("10.2875", 280.0, 305.0),
            _cell("2000", 320.0, 360.0),
            _cell("10.2280", 393.0, 418.0),
            _cell("1988.43", 433.0, 473.0),
        )
        mf = nsdl_p._parse_mf_holdings_row(block)
        assert mf is not None
        assert mf.value == Decimal("1988.43")
        assert mf.total_cost == Decimal("2000")

    def test_parse_mf_holdings_row_inf090i01wx1_near_par(self):
        """Real 1782131089 geometry: near-par value must not trigger secondary."""
        block = _block(
            _cell("INF090I01WX1\nFIMCFGP", 20.6, 75.0),
            _cell("Franklin India Multi\nCap Fund - Growth", 82.3, 145.0),
            _cell("34463878", 165.3, 198.0),
            _cell("999.950", 236.3, 235.0),
            _cell("10.0005", 298.9, 305.0),
            _cell("10,000.00", 361.1, 360.0),
            _cell("10.2603", 426.2, 451.0),
            _cell("10,259.79", 482.5, 524.0),
            _cell("259.79", 553.8, 574.0),
        )
        mf = nsdl_p._parse_mf_holdings_row(block)
        assert mf is not None
        assert mf.balance == Decimal("999.950")
        assert mf.nav == Decimal("10.2603")
        assert mf.value == Decimal("10259.79")
        assert mf.total_cost == Decimal("10000.00")
        assert mf.pnl == Decimal("259.79")
        assert mf.return_ is None

    def test_parse_mf_holdings_row_inf179k01cr2(self):
        """Real 1782131089 geometry: lakh-style value fragment must not become value."""
        block = _block(
            _cell("INF179K01CR2\nMFHDFC0078", 20.5, 75.0),
            _cell("HDFC Mid Cap\nFund - Regular Plan\n- Growth", 82.2, 145.0),
            _cell("31627597", 165.3, 198.0),
            _cell("300.762", 236.3, 235.0),
            _cell("199.4933", 297.0, 305.0),
            _cell("60,000.00", 360.6, 360.0),
            _cell("199.8970", 424.3, 451.0),
            _cell("60,121.42", 482.0, 524.0),
            _cell("121.42", 554.4, 574.0),
        )
        mf = nsdl_p._parse_mf_holdings_row(block)
        assert mf is not None
        assert mf.nav == Decimal("199.8970")
        assert mf.value == Decimal("60121.42")
        assert mf.pnl == Decimal("121.42")

    def test_parse_mf_holdings_row_inf194kb1aj8_small_balance(self):
        """Real 1782131089 geometry: spurious (avg,tc) pair left of true nav/value."""
        block = _block(
            _cell("INF194KB1AJ8\nNOT AVAILABLE", 20.5, 75.0),
            _cell("Bandhan Small Cap\nFund-Regular Plan Growth", 82.3, 145.0),
            _cell("8841793", 167.2, 198.0),
            _cell("62.931", 240.1, 235.0),
            _cell("47.6713", 298.3, 305.0),
            _cell("3,000.00", 362.6, 360.0),
            _cell("47.9400", 425.6, 451.0),
            _cell("3,016.91", 484.0, 524.0),
            _cell("16.91", 558.3, 574.0),
        )
        mf = nsdl_p._parse_mf_holdings_row(block)
        assert mf is not None
        assert mf.balance == Decimal("62.931")
        assert mf.nav == Decimal("47.9400")
        assert mf.value == Decimal("3016.91")
        assert mf.pnl == Decimal("16.91")

    def test_parse_mf_holdings_row_lakh_value_fragment(self):
        """Full lakh value plus truncated fragment — picker keeps the full amount."""
        block = _block(
            _cell("INF740KA1RB9", 20.0, 75.0),
            _cell("Lakh Fragment Fund", 80.0, 145.0),
            _cell("12345678", 167.0, 198.0),
            _cell("13,619.00", 204.0, 235.0),
            _cell("100.00", 280.0, 305.0),
            _cell("13,619.00", 320.0, 360.0),
            _cell("152.70", 393.0, 418.0),
            _cell("20,77,622.00", 433.0, 473.0),
            _cell("77,622.00", 492.0, 522.0),
            _cell("258.00", 557.0, 574.0),
            _cell("7.49", 600.0, 613.0),
        )
        mf = nsdl_p._parse_mf_holdings_row(block)
        assert mf is not None
        assert mf.value == Decimal("2077622.00")
        assert mf.pnl == Decimal("258.00")
        assert mf.return_ == Decimal("7.49")

    def test_parse_mf_holdings_row_returns_zero_placeholder(self):
        """A trailing 0.000 returns placeholder is ignored."""
        block = _block(
            _cell("INF000A01005", 20.0, 75.0),
            _cell("Zero Return Fund", 80.0, 145.0),
            _cell("12345678", 167.0, 198.0),
            _cell("100", 204.0, 235.0),
            _cell("10", 280.0, 305.0),
            _cell("1000", 320.0, 360.0),
            _cell("11", 393.0, 418.0),
            _cell("1100", 433.0, 473.0),
            _cell("100", 486.0, 522.0),
            _cell("0.000", 561.0, 574.0),
        )
        mf = nsdl_p._parse_mf_holdings_row(block)
        assert mf is not None
        assert mf.pnl == Decimal("100")
        assert mf.return_ is None

    def test_parse_mf_holdings_row_rejects_no_isin(self):
        block = _block(_cell("not-an-isin", 20.0, 75.0))
        assert nsdl_p._parse_mf_holdings_row(block) is None

    def test_parsers_reject_empty_block(self):
        """Every row parser returns None on a Block with zero cells."""
        empty = _block()
        assert nsdl_p._parse_equity_row(empty) is None
        assert nsdl_p._parse_summary_mf_row(empty) is None
        assert nsdl_p._parse_mf_holdings_row(empty) is None
        assert cdsl_p._parse_holdings_row(empty) is None
        assert cdsl_p._parse_mf_holdings_row(empty, {}) is None

    def test_parse_summary_mf_row_too_few_numerics(self):
        block = _block(
            _cell("INF000A01002"),
            _cell("Some Fund"),
            _cell("1"),
            _cell("2"),  # only 2 numerics, need ≥ 3
        )
        assert nsdl_p._parse_summary_mf_row(block) is None

    def test_find_period_returns_none(self):
        """`_find_period` returns None when no block's text matches."""
        blocks = [_block(_cell("nothing about a period here"))]
        assert nsdl_p._find_period(blocks) is None
        assert cdsl_p._find_period(blocks) is None

    def test_looks_numeric_handles_empty(self):
        """`_looks_numeric` returns False on empty / whitespace-only
        text (covers the early-out branch in both modules)."""
        assert not nsdl_p._looks_numeric("")
        assert not nsdl_p._looks_numeric("   ")
        assert not cdsl_p._looks_numeric("")
        assert not cdsl_p._looks_numeric("   ")

    def test_looks_numeric_leading_dot_both(self):
        """Sub-unit balances rendered without the leading zero
        (`.196`, `-.5`) must be recognised as numeric in BOTH NSDL and
        CDSL classifiers; the parsers share the regex shape."""
        for fn in (nsdl_p._looks_numeric, cdsl_p._looks_numeric):
            assert fn(".196")
            assert fn("-.5")
            # naked dot must still fail
            assert not fn(".")

    def test_per_account_header_joint_form(self):
        """The NSDL joint-account section header is split across THREE
        blocks: `NSDL Demat Account / ACCOUNT HOLDERS`, broker + first
        owner, then `DP ID:… Client ID:…` + second owner. The look-
        ahead in `_try_per_account_header` should resolve all three to
        a single account key."""
        blocks = [
            _block(
                _cell("NSDL Demat Account"),
                _cell("ACCOUNT HOLDERS"),
                page=11,
            ),
            _block(
                _cell("ACME BROKER LIMITED"),
                _cell("Holder One (PAN:ABCDE1234F)"),
                page=11,
            ),
            _block(
                _cell("DP ID: IN123456 Client ID: 99998888"),
                _cell("Holder Two (PAN:GHIJK5678L)"),
                page=11,
            ),
        ]
        key, consumed = nsdl_p._try_per_account_header(blocks, 0)
        assert key == ("NSDL", "IN123456", "99998888")
        # Three blocks consumed (the header + 2 look-ahead rows).
        assert consumed == 3

    def test_per_account_header_no_dpc_no_match(self):
        """A `NSDL Demat Account` block with no DP/Client info in itself
        or in the next few blocks → no match (returns None)."""
        blocks = [
            _block(_cell("NSDL Demat Account"), page=11),
            _block(_cell("Random unrelated text"), page=11),
            _block(_cell("Another random line"), page=11),
        ]
        key, consumed = nsdl_p._try_per_account_header(blocks, 0)
        assert key is None
        assert consumed == 1

    def test_per_account_header_not_demat(self):
        """A block that doesn't mention `NSDL|CDSL Demat Account` at
        all isn't a section header."""
        blocks = [_block(_cell("Just some text"), page=3)]
        key, consumed = nsdl_p._try_per_account_header(blocks, 0)
        assert key is None
        assert consumed == 1

    def test_summary_demat_row_4_cell(self):
        """Page-2 summary row where broker + DP/Client are joined in a
        single cell with a newline (4 cells total)."""
        block = _block(
            _cell("NSDL Demat Account"),
            _cell("ACME BROKER LIMITED\nDP ID: IN123456 Client ID: 99998888"),
            _cell("12"),
            _cell("1,04,00,929.50"),
            page=2,
        )
        assert nsdl_p._is_summary_demat_row(block)
        ac, key = nsdl_p._account_from_summary_row(block, owners=[])
        assert key == ("NSDL", "IN123456", "99998888")
        assert ac.name == "ACME BROKER LIMITED"
        assert ac.dp_id == "IN123456"
        assert ac.client_id == "99998888"
        assert ac.folios == 12
        assert ac.balance == Decimal("10400929.50")

    def test_summary_demat_row_5_cell(self):
        """5-cell variant: broker name and DP/Client line as separate
        cells (observed on CDSL rows in some NSDL CAS layouts)."""
        block = _block(
            _cell("CDSL Demat Account"),
            _cell("BETA BROKER LIMITED"),
            _cell("DP ID:11112222 Client ID:33334444"),
            _cell("25"),
            _cell("97,34,823.11"),
            page=2,
        )
        assert nsdl_p._is_summary_demat_row(block)
        ac, key = nsdl_p._account_from_summary_row(block, owners=[])
        assert key == ("CDSL", "11112222", "33334444")
        assert ac.name == "BETA BROKER LIMITED"
        assert ac.folios == 25
        assert ac.balance == Decimal("9734823.11")

    def test_summary_demat_row_rejects_wrong_cell_count(self):
        # 3 cells: too short.
        block = _block(
            _cell("NSDL Demat Account"),
            _cell("BROKER\nDP ID: IN123456 Client ID: 99998888"),
            _cell("12"),
            page=2,
        )
        assert not nsdl_p._is_summary_demat_row(block)

    def test_parse_bond_summary_row(self):
        """NSDL-flavour summary bonds row — discriminates frequency
        (text) from coupon-rate (numeric) within the shared x-band."""
        block = _block(
            _cell("INE000A07001", 20.7, 67.1),
            _cell("EXAMPLE BOND\nISSUER\nLIMITED", 93.2, 168.2),
            _cell("Once a year", 185.8, 223.7),  # frequency text
            _cell("8.10", 198.0, 211.0),  # coupon rate numeric
            _cell("05-Mar-2022", 250.9, 290.3),
            _cell("200", 354.3, 365.4),
            _cell("1,000.00", 442.6, 468.7),
            _cell("2,00,000.00", 538.2, 574.7),
        )
        bd = nsdl_p._parse_bond_summary_row(block)
        assert bd is not None
        assert bd.isin == "INE000A07001"
        assert bd.name == "EXAMPLE BOND ISSUER LIMITED"
        assert bd.coupon_rate == Decimal("8.10")
        assert bd.coupon_frequency == "Once a year"
        assert bd.maturity_date == "05-Mar-2022"
        assert bd.num_bonds == Decimal("200")
        assert bd.face_value == Decimal("1000.00")
        assert bd.value == Decimal("200000.00")
        # Detailed-only fields stay None.
        assert bd.market_price is None

    def test_parse_bond_summary_row_rejects_non_isin(self):
        block = _block(_cell("Not an ISIN"), _cell("..."))
        assert nsdl_p._parse_bond_summary_row(block) is None

    def test_parse_bond_summary_row_rejects_empty(self):
        block = _block()
        assert nsdl_p._parse_bond_summary_row(block) is None

    def test_parse_bond_detailed_row(self):
        """CDSL-flavour 13-cell detailed bonds row."""
        block = _block(
            _cell("INE000A07002"),
            _cell("EXAMPLE BOND ISSUER LIMITED 8.71% NCD"),
            _cell("100.000"),
            _cell("100.000"),
            _cell("0.000"),
            _cell("0.000"),
            _cell("0.000"),
            _cell("0.000"),
            _cell("0.000"),
            _cell("0.000"),
            _cell("0.000"),
            _cell("1,276.47"),
            _cell("1,27,647.00"),
        )
        bd = nsdl_p._parse_bond_detailed_row(block)
        assert bd is not None
        assert bd.isin == "INE000A07002"
        assert bd.num_bonds == Decimal("100.000")
        assert bd.market_price == Decimal("1276.47")
        assert bd.value == Decimal("127647.00")
        # Summary-only fields stay None.
        assert bd.coupon_rate is None
        assert bd.face_value is None
        assert bd.maturity_date is None

    def test_parse_bond_detailed_row_rejects_non_isin(self):
        block = _block(_cell("Subtotal"), _cell("..."))
        assert nsdl_p._parse_bond_detailed_row(block) is None

    def test_parse_detailed_mf_row(self):
        """CDSL-flavour 'Mutual Funds (M)' detailed row — INF ISIN."""
        block = _block(
            _cell("INF000A01001"),
            _cell("EXAMPLE FUND HOUSE"),
            _cell("22,994.003"),
            _cell("22,994.003"),
            _cell("0.000"),
            _cell("0.000"),
            _cell("0.000"),
            _cell("0.000"),
            _cell("0.000"),
            _cell("0.000"),
            _cell("0.000"),
            _cell("22.55"),
            _cell("5,18,399.80"),
        )
        mf = nsdl_p._parse_detailed_mf_row(block)
        assert mf is not None
        assert mf.isin == "INF000A01001"
        assert mf.balance == Decimal("22994.003")
        assert mf.nav == Decimal("22.55")
        assert mf.value == Decimal("518399.80")

    def test_parse_detailed_mf_row_rejects_equity_isin(self):
        """Equity ISIN (INE…) must NOT match the MF detailed parser."""
        block = _block(
            _cell("INE000A07002"),
            _cell("Some equity"),
            _cell("100"),
            _cell("1000"),
            _cell("100000"),
        )
        assert nsdl_p._parse_detailed_mf_row(block) is None


class TestISINSearchFallback:
    """The direct-ISIN fallback path activates when the primary
    (scheme, rta, rta_code) lookup misses but the caller hinted at an
    inline ISIN parsed from the scheme header."""

    def test_direct_isin_fallback(self):
        from casparser.parsers._isin import isin_search

        # Garbage rta/rta_code but real ISIN → fallback path resolves.
        isin, amfi, scheme_type = isin_search(
            "scheme name doesn't matter",
            "BAD_RTA",
            "bogus_code",
            isin="INF846K01EW2",
        )
        assert isin == "INF846K01EW2"
        assert amfi == "120503"
        assert scheme_type == "EQUITY"

    def test_direct_isin_fallback_unknown_isin(self):
        """An unknown ISIN with no other lookup keys returns nones."""
        from casparser.parsers._isin import isin_search

        isin, amfi, scheme_type = isin_search(
            "",
            "BAD",
            "bogus",
            isin="INF000X00X00",
        )
        # No match anywhere → all None.
        assert isin is None
        assert amfi is None
        assert scheme_type is None


class TestBatchISINMetadata:
    """`batch_isin_metadata` backfills (amfi, type) for demat MF holdings,
    which depository statements carry by ISIN only."""

    def test_resolves_known_isins(self):
        from casparser.parsers._isin import batch_isin_metadata

        # Duplicate + falsy entries are de-duped / ignored.
        meta = batch_isin_metadata(["INF846K01EW2", "INF846K01EW2", "", "INF174V01317"])
        assert meta["INF846K01EW2"] == ("120503", "EQUITY")
        assert meta["INF174V01317"] == ("141224", "EQUITY")

    def test_unknown_isin_maps_to_nones(self):
        from casparser.parsers._isin import batch_isin_metadata

        meta = batch_isin_metadata(["INF000X00X00"])
        assert meta["INF000X00X00"] == (None, None)

    def test_empty_input_returns_empty(self):
        from casparser.parsers._isin import batch_isin_metadata

        assert batch_isin_metadata([]) == {}
        assert batch_isin_metadata(["", None]) == {}


def _atom(text: str, x_left=100.0, x_right=200.0, y_top=500.0, y_bot=490.0) -> Atom:
    """Synthetic Atom for column-join tests."""
    return Atom(x_left, x_right, y_top, y_bot, text, "Helvetica", stream_seq=0)


class TestSoftHyphen:
    """Reconstruction of tokens (notably ISINs) that a CAS generator
    soft-wrapped across lines with a U+00AD soft hyphen. Regression
    guard for CDSL issue #127 — `INF179K01<SHY>WN9` was being dropped
    because neither the wrapped fragment nor the leftover matched the
    anchored ISIN regex."""

    def test_embedded_soft_hyphen_single_atom(self):
        """A single atom carrying an embedded soft hyphen
        (`INF179K01\\u00adWN9`) is normalised to the clean ISIN."""
        out = _join_column_atoms([_atom(f"INF179K01{SOFT_HYPHEN}WN9")])
        assert out == "INF179K01WN9"
        assert cdsl_p.INF_ISIN_RE.match(out)

    def test_trailing_soft_hyphen_split_across_two_atoms(self):
        """The wrap case: `INF179K01\\u00ad` on one line, `WN9` on the
        next (same column). The trailing soft hyphen splices the
        continuation on with no separator."""
        atoms = [
            _atom(f"INF179K01{SOFT_HYPHEN}", y_top=500.0, y_bot=492.0),
            _atom("WN9", y_top=491.0, y_bot=483.0),
        ]
        out = _join_column_atoms(atoms)
        assert out == "INF179K01WN9"
        assert cdsl_p.INF_ISIN_RE.match(out)

    def test_normal_multiline_cell_unchanged(self):
        """A genuine multi-line cell (scheme name) with no soft hyphen
        still joins with newlines — no behavioural change."""
        atoms = [
            _atom("HDFC Small Cap Fund -", y_top=500.0, y_bot=492.0),
            _atom("Direct Growth Plan", y_top=491.0, y_bot=483.0),
        ]
        out = _join_column_atoms(atoms)
        assert out == "HDFC Small Cap Fund -\nDirect Growth Plan"

    def test_chained_continuation(self):
        """Defensive: a token wrapped across three fragments still
        reconstructs (two consecutive soft-hyphen continuations)."""
        atoms = [
            _atom(f"INF179{SOFT_HYPHEN}"),
            _atom(f"K01{SOFT_HYPHEN}"),
            _atom("WN9"),
        ]
        assert _join_column_atoms(atoms) == "INF179K01WN9"

    def test_cells_from_block_reconstructs_isin(self):
        """End-to-end through `_cells_from_block_atoms`: two same-column
        atoms (the soft-hyphen-wrapped ISIN) collapse into one cell
        whose text is a valid ISIN."""
        atoms = [
            _atom(f"INF179K01{SOFT_HYPHEN}", x_left=100, x_right=200, y_top=500, y_bot=492),
            _atom("WN9", x_left=100, x_right=180, y_top=491, y_bot=483),
        ]
        cells = _cells_from_block_atoms(atoms)
        assert len(cells) == 1
        assert cells[0].text == "INF179K01WN9"
        assert cdsl_p.INF_ISIN_RE.match(cells[0].text)


class TestBatchEquitySymbols:
    """`batch_equity_symbols` backfills (symbol, exchange) for demat equity
    holdings, which depository statements carry by ISIN only."""

    @staticmethod
    def _isin_db_with_symbols(path):
        import sqlite3
        from contextlib import closing

        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute(
                "CREATE TABLE isin(isin NOT NULL PRIMARY KEY, name, issuer, type, "
                "status, symbol, exchange, last_seen)"
            )
            conn.executemany(
                "INSERT INTO isin(isin, name, issuer, type, status, symbol, exchange, "
                "last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        "INE002A01018",
                        "Reliance",
                        "RIL",
                        "EQUITY SHARES",
                        "ACTIVE",
                        "RELIANCE",
                        "NSE",
                        "2026-06-01",
                    ),
                    # A bond with no listed symbol -- must not appear in the map.
                    (
                        "INE111A07011",
                        "Some SGB",
                        "RBI",
                        "SOVEREIGN GOLD BOND",
                        "ACTIVE",
                        None,
                        None,
                        "2026-06-01",
                    ),
                ],
            )

    def test_resolves_symbols(self, tmp_path, monkeypatch):
        from casparser.parsers._isin import batch_equity_symbols

        db = tmp_path / "isin.db"
        self._isin_db_with_symbols(db)
        monkeypatch.setenv("CASPARSER_ISIN_DB", str(db))
        out = batch_equity_symbols(["INE002A01018", "INE002A01018", "", "INE111A07011"])
        assert out["INE002A01018"] == ("RELIANCE", "NSE")
        # Bond carries no symbol -> excluded entirely.
        assert "INE111A07011" not in out

    def test_unknown_isin_absent(self, tmp_path, monkeypatch):
        from casparser.parsers._isin import batch_equity_symbols

        db = tmp_path / "isin.db"
        self._isin_db_with_symbols(db)
        monkeypatch.setenv("CASPARSER_ISIN_DB", str(db))
        assert batch_equity_symbols(["INE000X00X00"]) == {}

    def test_legacy_db_without_symbol_columns_returns_empty(self, tmp_path, monkeypatch):
        # A DB built before the symbol columns existed resolves nothing here
        # (graceful degradation) rather than raising.
        import sqlite3
        from contextlib import closing

        from casparser.parsers._isin import batch_equity_symbols

        db = tmp_path / "isin.db"
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute(
                "CREATE TABLE isin(isin NOT NULL PRIMARY KEY, name, issuer, type, "
                "status, last_seen)"
            )
            conn.execute(
                "INSERT INTO isin VALUES (?, ?, ?, ?, ?, ?)",
                ("INE002A01018", "Reliance", "RIL", "EQUITY SHARES", "ACTIVE", "2026-06-01"),
            )
        monkeypatch.setenv("CASPARSER_ISIN_DB", str(db))
        assert batch_equity_symbols(["INE002A01018"]) == {}

    def test_empty_input_returns_empty(self):
        from casparser.parsers._isin import batch_equity_symbols

        assert batch_equity_symbols([]) == {}
        assert batch_equity_symbols(["", None]) == {}


class TestEquityModelSymbolFields:
    """The Equity model gained symbol/exchange; construction stays robust."""

    def test_symbol_exchange_default_none_and_decimals_still_parse(self):
        from casparser.types import Equity

        eq = Equity(isin="INE002A01018", num_shares="1,000", price="1,234.50", value="12,34,500")
        assert eq.symbol is None
        assert eq.exchange is None
        assert eq.num_shares == Decimal("1000")
        assert eq.price == Decimal("1234.50")

    def test_constructing_with_symbol_does_not_break_fix_float(self):
        from casparser.types import Equity

        eq = Equity(
            isin="INE002A01018",
            num_shares="5",
            price="10",
            value="50",
            symbol="RELIANCE",
            exchange="NSE",
        )
        assert eq.symbol == "RELIANCE"
        assert eq.exchange == "NSE"


class TestEnrichDematEquities:
    """End-to-end backfill: parsed equities get a symbol from the ISIN DB."""

    def test_enriches_equity_symbol(self, tmp_path, monkeypatch):
        import sqlite3
        from contextlib import closing

        from casparser.enums import FileType
        from casparser.parsers import _enrich_demat_equities
        from casparser.types import (
            DematAccount,
            DematOwner,
            Equity,
            InvestorInfo,
            NSDLCASData,
            StatementPeriod,
        )

        db = tmp_path / "isin.db"
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute(
                "CREATE TABLE isin(isin NOT NULL PRIMARY KEY, name, issuer, type, "
                "status, symbol, exchange, last_seen)"
            )
            conn.execute(
                "INSERT INTO isin VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "INE002A01018",
                    "Reliance",
                    "RIL",
                    "EQUITY SHARES",
                    "ACTIVE",
                    "RELIANCE",
                    "NSE",
                    "2026-06-01",
                ),
            )
        monkeypatch.setenv("CASPARSER_ISIN_DB", str(db))

        data = NSDLCASData(
            accounts=[
                DematAccount(
                    name="ACME Demat",
                    type="NSDL",
                    folios=1,
                    balance=Decimal("100"),
                    owners=[DematOwner(name="A B", PAN="ABCDE1234F")],
                    equities=[
                        Equity(isin="INE002A01018", num_shares="10", price="10", value="100"),
                        Equity(isin="INE000X00X00", num_shares="5", price="2", value="10"),
                    ],
                    mutual_funds=[],
                )
            ],
            statement_period=StatementPeriod(**{"from": "2026-01-01", "to": "2026-03-31"}),
            investor_info=InvestorInfo(name="A B", email="a@b.com", address="x", mobile="9"),
            file_type=FileType.NSDL,
        )
        out = _enrich_demat_equities(data)
        eqs = {e.isin: e for e in out.accounts[0].equities}
        assert eqs["INE002A01018"].symbol == "RELIANCE"
        assert eqs["INE002A01018"].exchange == "NSE"
        # Unresolved ISIN stays None, doesn't raise.
        assert eqs["INE000X00X00"].symbol is None


class TestNPS:
    """CDSL NPS holdings parser (`cdsl._parse_nps`). Blocks mirror the real
    layout: each scheme spans a name-line-1 (+fund-mgr-1), an interleaved
    units/nav line, and a name-line-2 (+fund-mgr-2)."""

    @staticmethod
    def _scheme_blocks(asset: str, tier_word: str, units: str, nav: str, y: int):
        # name/fund-manager wrap across two lines; numerics sit between them.
        return [
            _block(
                _cell("NPS TRUST- A/C HDFC PENSION FUND", 223, 2000, y, y - 40),
                _cell("HDFC PENSION FUND MANAGEMENT", 2223, 3800, y, y - 40),
            ),
            _block(
                _cell(units, 4379, 5100, y - 45, y - 85),
                _cell(nav, 5457, 5900, y - 45, y - 85),
            ),
            _block(
                _cell(
                    f"MANAGEMENT LIMITED SCHEME {asset} - TIER {tier_word}",
                    223,
                    2000,
                    y - 90,
                    y - 130,
                ),
                _cell("LIMITED", 2223, 3000, y - 90, y - 130),
            ),
        ]

    def _nps_blocks(self):
        blocks = [
            _block(_cell("NPS-SP : PROT PRAN ID : 110099887766", 205, 4000, 7000, 6960)),
            _block(
                _cell(
                    "STATEMENT OF TRANSACTIONS FOR THE PERIOD FROM 01-04-2025 TO 31-03-2026",
                    1048,
                    5000,
                    6800,
                    6760,
                )
            ),
            # a transaction row mentioning NPS TRUST — must NOT be parsed as a holding
            _block(
                _cell("02-Feb-2026", 218, 700, 6700, 6660),
                _cell(
                    "NPS TRUST- A/C HDFC PENSION FUND MANAGEMENT LIMITED SCHEME G - TIER I",
                    829,
                    2000,
                    6700,
                    6660,
                ),
                _cell("CR", 3551, 3700, 6700, 6660),
                _cell("17,828.64", 4177, 4600, 6700, 6660),
            ),
            _block(_cell("HOLDING STATEMENT AS ON 31-03-2026", 2044, 4000, 6392, 6352)),
        ]
        blocks += self._scheme_blocks("G", "I", "45,982.3138", "27.6140", 6137)
        blocks += self._scheme_blocks("E", "I", "12,000.0000", "50.0000", 5900)
        blocks.append(
            _block(_cell("Portfolio Value ` 8,69,755.61 as on 31-03-2026", 239, 3000, 5433, 5393))
        )
        return blocks

    def test_parse_nps_holdings(self):
        nps = cdsl_p._parse_nps(self._nps_blocks())
        assert nps is not None
        assert nps.nps_sp == "PROT"
        assert nps.pran == "110099887766"
        assert nps.value == Decimal("869755.61")
        assert len(nps.schemes) == 2  # transaction row not counted
        g, e = nps.schemes
        assert g.asset_class == "G" and g.tier == "I"
        assert g.units == Decimal("45982.3138") and g.nav == Decimal("27.6140")
        assert g.value == Decimal("1269755.61")  # units * nav
        assert g.fund_manager == "HDFC PENSION FUND MANAGEMENT LIMITED"
        assert e.asset_class == "E" and e.value == Decimal("600000.00")

    def test_parse_nps_skips_redacted_scheme(self):
        # A scheme whose units/nav are blank (redacted) yields no numerics -> skipped.
        blocks = [
            _block(_cell("HOLDING STATEMENT AS ON 31-03-2026", 2044, 4000, 6392, 6352)),
            _block(
                _cell("NPS TRUST- A/C HDFC PENSION FUND", 223, 2000, 6137, 6097),
                _cell("HDFC PENSION FUND MANAGEMENT", 2223, 3800, 6137, 6097),
            ),
            _block(_cell("MANAGEMENT LIMITED SCHEME C - TIER I", 223, 2000, 6046, 6006)),
            _block(_cell("Portfolio Value ` 5,00,000.00 as on 31-03-2026", 239, 3000, 5433, 5393)),
        ]
        nps = cdsl_p._parse_nps(blocks)
        assert nps is not None
        assert nps.value == Decimal("500000.00")
        assert nps.schemes == []

    def test_parse_nps_absent_returns_none(self):
        blocks = [
            _block(_cell("HOLDING STATEMENT AS ON 31-03-2026", 2044, 4000, 100, 60)),
            _block(_cell("INE000A01001", 20, 75, 50, 10), _cell("SOME EQUITY", 80, 200, 50, 10)),
        ]
        assert cdsl_p._parse_nps(blocks) is None

    def test_parse_nps_scheme_spanning_page_break(self):
        """A scheme row straddling a page break must not absorb the next
        page's furniture (bank header, tab bar, investor name, column header,
        footer), and the scheme/fund-manager split must not depend on a fixed
        x (fund manager here sits at x=1000, below the old threshold)."""
        blocks = [
            _block(_cell("HOLDING STATEMENT AS ON 31-05-2026", 2044, 4000, 700, 660)),
            # scheme line 1 (name @200, fund-manager @1000) + units/nav
            _block(
                _cell("NPS TRUST A/C HDFC PENSION FUND", 200, 900, 600, 560),
                _cell("HDFC PENSION FUND MANAGEMENT", 1000, 1800, 600, 560),
            ),
            _block(
                _cell("45,982.3138", 4000, 4600, 555, 515), _cell("27.6140", 5000, 5400, 555, 515)
            ),
            # --- page break: furniture that must be skipped ---
            _block(_cell("Central Depository Services (India) Limited", 2000, 3500, 550, 510)),
            _block(
                _cell(
                    "CONSOLIDATED ACCOUNT STATEMENT (CAS) FOR SECURITIES HELD IN DEMAT",
                    800,
                    4000,
                    540,
                    500,
                )
            ),
            _block(_cell("VINEET MENON", 250, 900, 530, 490)),
            _block(
                _cell("Scheme Name", 800, 1500, 520, 480),
                _cell("Fund Manager", 2000, 2800, 520, 480),
            ),
            _block(_cell("Page 18 of 21", 500, 900, 510, 470)),
            # scheme line 2 (continuation) + fund-manager line 2
            _block(
                _cell("MANAGEMENT LIMITED SCHEME G - TIER I GS", 200, 900, 500, 460),
                _cell("LIMITED", 1000, 1400, 500, 460),
            ),
            _block(_cell("Portfolio Value ` 12,69,755.61 as on 31-05-2026", 239, 3000, 440, 400)),
        ]
        nps = cdsl_p._parse_nps(blocks)
        assert nps is not None
        assert len(nps.schemes) == 1
        s = nps.schemes[0]
        assert s.scheme == "NPS TRUST A/C HDFC PENSION FUND MANAGEMENT LIMITED SCHEME G - TIER I GS"
        assert s.fund_manager == "HDFC PENSION FUND MANAGEMENT LIMITED"
        assert s.asset_class == "G" and s.tier == "I"
        assert s.units == Decimal("45982.3138") and s.nav == Decimal("27.6140")
        # no page furniture leaked into the scheme name
        for junk in ("VINEET", "Central", "Page", "CONSOLIDATED", "Scheme Name"):
            assert junk not in s.scheme
