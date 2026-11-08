"""Unit tests for CAMS/KFin SUMMARY numeric cells wider than NUMERIC_WIDTH.

`cams_summary.parse` runs on synthetic `Line`s (extraction monkeypatched).
An amount of ₹1 crore or more ("1,00,00,000.00") starts further left than
the 42pt numeric zone; its leading digit must stay with the number, not
drop out or join the scheme name.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from casparser.parsers import cams_summary
from casparser.parsers.extract import Char, Line, Page
from casparser.types import InvestorInfo

ADV = 3.9  # glyph advance (pts); a space advances the pen by one more
CHAR_H = 5.0  # word gap = max(1.5, 0.6 × CHAR_H) = 3pt
TEXT_GAP = 0.5  # gap between glyph boxes inside a word
NUM_GAP = 2.0  # inside a number: PDFium boxes leave ~2pt around "1" or ","


def L(baseline: float, *segs: tuple[float, str, float]) -> Line:
    """A synthetic Line from (x0, text, glyph gap) segments. Spaces
    advance the pen but emit no Char."""
    chars = []
    for x0, text, gap in segs:
        for i, ch in enumerate(text):
            if ch != " ":
                x = x0 + i * ADV
                chars.append(
                    Char(text=ch, x0=x, y0=baseline, x1=x + ADV - gap, y1=baseline + CHAR_H)
                )
    return Line(page=1, baseline=baseline, chars=chars)


def left(x0: float, text: str) -> tuple[float, str, float]:
    return (x0, text, TEXT_GAP)


def right(x_hi: float, text: str, gap: float = TEXT_GAP) -> tuple[float, str, float]:
    """Right-aligned: the last glyph box ends at x_hi."""
    return (x_hi - ADV * len(text) + gap, text, gap)


def number(x_hi: float, text: str) -> tuple[float, str, float]:
    return right(x_hi, text, NUM_GAP)


# Column edges: left edges for text, right edges for numbers (CAMS layout).
COST_R, BAL_R, NAV_R, VALUE_R = 470, 540, 650, 730
HEADER = L(
    700,
    left(30, "Folio No."),
    left(92, "ISIN"),
    left(156, "Scheme Name"),
    right(COST_R, "Cost Value"),
    right(BAL_R, "Unit Balance"),
    left(556, "NAV Date"),
    right(NAV_R, "NAV"),
    right(VALUE_R, "Market Value"),
    left(748, "Registrar"),
)


def row(cost: str, value: str, *, units: str = "4,000.000", scheme=None) -> Line:
    return L(
        680,
        left(30, "91234567"),
        left(92, "INFZ90K01LQ6"),
        scheme or left(156, "FLQDG-Fictiva Liquid Fund - Direct Plan - Growth"),
        number(COST_R, cost),
        number(BAL_R, units),
        left(556, "31-Mar-2025"),
        number(NAV_R, "2,911.4521"),
        number(VALUE_R, value),
        left(748, "CAMS"),
    )


@pytest.fixture
def parse_row(monkeypatch):
    def run(line: Line):
        monkeypatch.setattr(
            cams_summary, "extract_pages", lambda *a, **k: [Page(number=1, lines=[HEADER, line])]
        )
        monkeypatch.setattr(
            cams_summary,
            "extract_cams_kfin_investor",
            lambda *a, **k: InvestorInfo(name="X", email="", address="", mobile=""),
        )
        (folio,) = cams_summary.parse("synthetic.pdf", "").folios
        (scheme,) = folio.schemes
        return scheme

    return run


@pytest.mark.parametrize("amount", ["1,00,00,000.00", "12,34,56,789.01", "1,00,00,00,000.00"])
def test_wide_cost_and_value_read_whole(parse_row, amount):
    scheme = parse_row(row(amount, amount))
    expected = Decimal(amount.replace(",", ""))
    assert scheme.valuation.cost == expected
    assert scheme.valuation.value == expected
    assert scheme.scheme == "Fictiva Liquid Fund - Direct Plan - Growth"
    assert scheme.close == Decimal("4000.000")
    assert scheme.valuation.nav == Decimal("2911.4521")


def test_wide_unit_balance_reads_whole(parse_row):
    scheme = parse_row(row("1,10,00,000.00", "1,16,45,808.40", units="12,34,567.890"))
    assert scheme.close == Decimal("1234567.890")


@pytest.mark.parametrize("space_glyph", [False, True])
def test_name_ending_in_digits_stays_apart_from_cost(parse_row, space_glyph):
    # The scheme name ends one space before a crore cost, optionally with
    # the space as a glyph whose box fills the gap.
    cost = "1,10,00,000.00"
    cost_x0 = number(COST_R, cost)[0]
    name = right(cost_x0 - ADV - TEXT_GAP, "FFT12-Fictiva Fixed Term Plan - Series 12")
    line = row(cost, "1,16,45,808.40", scheme=name)
    if space_glyph:
        line.chars.append(Char(text=" ", x0=cost_x0 - ADV, y0=680, x1=cost_x0, y1=680 + CHAR_H))
    scheme = parse_row(line)
    assert scheme.scheme == "Fictiva Fixed Term Plan - Series 12"
    assert scheme.valuation.cost == Decimal("11000000.00")


def test_letters_touching_a_number_stay_out_of_it(parse_row):
    # Only digits, "," and "." join a number: a name ending right at a
    # crore cost keeps its last word.
    cost = "1,10,00,000.00"
    name = right(
        number(COST_R, cost)[0] - TEXT_GAP, "FLQDG-Fictiva Liquid Fund - Direct Plan - Growth"
    )
    scheme = parse_row(row(cost, "1,16,45,808.40", scheme=name))
    assert scheme.scheme == "Fictiva Liquid Fund - Direct Plan - Growth"
    assert scheme.valuation.cost == Decimal("11000000.00")
