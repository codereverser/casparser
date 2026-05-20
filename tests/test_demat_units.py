"""Unit tests for NSDL/CDSL parser helpers — exercise the corner-case
branches that the end-to-end NSDL+CDSL fixtures don't hit (decimal
parsing edge cases, joint-name owner block, summary-table row
recognisers, MF holdings row anomaly handling, etc.)."""

from __future__ import annotations

from decimal import Decimal

import pytest

import casparser.parsers.cdsl as cdsl_p
import casparser.parsers.nsdl as nsdl_p
from casparser.parsers.pageobj import Atom, Block, Cell


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
        # The misplaced `8` is folded into UCC since the ISIN cell's
        # UCC line was the placeholder `NOT AVAILABLE`.
        assert mf.ucc == "8"

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
