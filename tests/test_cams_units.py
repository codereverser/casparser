"""Unit tests for the CAMS/KFin DETAILED wrapped-description merge (issue #118).

A description too wide for the Transaction column wraps onto its own
physical line directly below the row. `cams_detailed.parse` merges such a
line into the previous transaction's description; these tests drive the
real `parse()` loop with synthetic `Line`s (extraction monkeypatched) so
the adjacency, gap, and cell-shape guards are exercised end to end.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from casparser.parsers import cams_detailed
from casparser.parsers.cams_detailed import _continuation_text, parse
from casparser.parsers.extract import Char, Line, Page
from casparser.types import InvestorInfo

# ------------------------------------------------------------- line builder

CHAR_W = 3.5  # glyph advance; word gaps advance 2× so Line.text re-spaces
CHAR_H = 8.0


def _seg_chars(x0: float, text: str, baseline: float) -> list[Char]:
    """Chars for one text segment starting at x0. Spaces advance the pen
    (wide enough for Line.text to re-insert them) but emit no Char, like
    real extraction."""
    chars, x = [], x0
    for ch in text:
        if ch == " ":
            x += 2 * CHAR_W
            continue
        chars.append(Char(text=ch, x0=x, y0=baseline, x1=x + CHAR_W, y1=baseline + CHAR_H))
        x += CHAR_W
    return chars


def L(baseline: float, *segs: tuple[float, str]) -> Line:
    """A synthetic Line from (x0, text) segments. For right-aligned cells
    pass R() as the x0."""
    chars = []
    for x0, text in segs:
        chars.extend(_seg_chars(x0, text, baseline))
    return Line(page=1, baseline=baseline, chars=chars)


def R(x_hi: float, text: str) -> float:
    """x0 that right-aligns `text` at x_hi (no spaces in numerics)."""
    return x_hi - CHAR_W * len(text)


# Column header geometry (echoes the real CAMS layout: Date/Transaction
# left-aligned, numerics right-aligned, "Unit"/"Balance" stacked).
HEADER_TOP = [
    (30, "Date"),
    (75, "Transaction"),
    (R(360, "Amount"), "Amount"),
    (R(425, "Units"), "Units"),
    (R(485, "Price"), "Price"),
    (R(550, "Unit"), "Unit"),
]
HEADER_BOT = [
    (R(360, "(INR)"), "(INR)"),
    (R(485, "(INR)"), "(INR)"),
    (R(550, "Balance"), "Balance"),
]


def _statement(rows: list[Line]) -> list[Page]:
    """One synthetic page: period, AMC, folio, scheme header, opening
    balance, column header, `rows`, closing balance + footer."""
    lines = [
        L(780, (30, "01-Jan-2024 To 30-Jun-2024")),
        L(770, (30, "Test Mutual Fund")),
        L(760, (30, "Folio No : 12345 / 67")),
        L(750, (75, "T123-Test Fund - Direct Growth - ISIN: INF123A01234 Registrar : CAMS")),
        L(740, (400, "Opening Unit Balance: 0.000")),
        L(700, *HEADER_TOP),
        L(692, *HEADER_BOT),
        *rows,
        L(
            560,
            (30, "Closing Unit Balance: 5.000 NAV on 30-Jun-2024: INR 100.0000"),
            (330, "Valuation on 30-Jun-2024: INR 500.00"),
        ),
    ]
    return [Page(number=1, lines=lines)]


@pytest.fixture
def parse_lines(monkeypatch):
    def run(rows: list[Line]):
        monkeypatch.setattr(cams_detailed, "extract_pages", lambda *a, **k: _statement(rows))
        monkeypatch.setattr(
            cams_detailed,
            "extract_cams_kfin_investor",
            lambda *a, **k: InvestorInfo(name="X", email="", address="", mobile=""),
        )
        return parse("synthetic.pdf", "")

    return run


PURCHASE_ROW = L(
    680,
    (30, "01-Jan-2024"),
    (75, "Purchase - BSE -"),
    (R(360, "1,000.00"), "1,000.00"),
    (R(425, "10.000"), "10.000"),
    (R(485, "100.0000"), "100.0000"),
    (R(550, "10.000"), "10.000"),
)
REDEMPTION_ROW = L(
    646,
    (30, "03-Jan-2024"),
    (75, "Redemption -"),
    (R(360, "(500.00)"), "(500.00)"),
    (R(425, "(5.000)"), "(5.000)"),
    (R(485, "100.0000"), "100.0000"),
    (R(550, "5.000"), "5.000"),
)


def _txns(data):
    return [t for f in data.folios for s in f.schemes for t in s.transactions]


# ------------------------------------------------------------ cell predicate


class TestContinuationText:
    def test_transaction_only(self):
        assert _continuation_text({"Transaction": "Instalment 5/18"}) == "Instalment 5/18"

    def test_other_columns_disqualify(self):
        assert _continuation_text({"Transaction": "Instalment 5/18", "Amount": "1.00"}) is None
        assert _continuation_text({"Date": "x", "Transaction": "y"}) is None
        assert _continuation_text({"Unit Balance": "5.000"}) is None

    def test_empty(self):
        assert _continuation_text({}) is None
        assert _continuation_text({"Transaction": "  "}) is None


# -------------------------------------------------------------- merge logic


class TestContinuationMerge:
    def test_wrapped_tail_merges_and_reclassifies(self, parse_lines):
        # 7.5pt below the Purchase row, Transaction column only — the
        # canonical wrap. "Instalment" flips PURCHASE → PURCHASE_SIP.
        rows = [PURCHASE_ROW, L(672.5, (75, "Instalment 5/18")), REDEMPTION_ROW]
        txns = _txns(parse_lines(rows))
        assert len(txns) == 2
        assert txns[0].description == "Purchase - BSE - Instalment 5/18"
        assert txns[0].type == "PURCHASE_SIP"
        assert txns[0].amount == Decimal("1000.00")
        assert txns[1].type == "REDEMPTION"

    def test_stacked_continuations_chain(self, parse_lines):
        rows = [
            PURCHASE_ROW,
            L(672.5, (75, "Instalment 5/18")),
            L(665.0, (75, "via Internet")),
            REDEMPTION_ROW,
        ]
        txns = _txns(parse_lines(rows))
        assert txns[0].description == "Purchase - BSE - Instalment 5/18 via Internet"

    def test_marker_row_emits_misc_and_absorbs_its_tail(self, parse_lines):
        # A dated ***marker*** row (no amount, no units) is emitted as a
        # MISC transaction; the wrap below it merges into IT — never into
        # the Purchase above (the original issue-#118 report's row).
        rows = [
            PURCHASE_ROW,
            L(672.5, (30, "02-Jan-2024"), (75, "***Registration of Nominee***")),
            L(665.0, (75, "MFC-12345-98765***")),
            REDEMPTION_ROW,
        ]
        txns = _txns(parse_lines(rows))
        assert len(txns) == 3
        assert txns[0].description == "Purchase - BSE -"
        misc = txns[1]
        assert misc.type == "MISC"
        assert misc.description == "***Registration of Nominee*** MFC-12345-98765***"
        assert misc.amount is None and misc.units is None and misc.nav is None

    def test_stray_dated_footnote_still_skipped(self, parse_lines):
        # Dated, but no ***, no amount/units, and no printed balance —
        # the stray-footnote shape stays out of the transaction list,
        # and its follow-up line is dropped with it.
        rows = [
            PURCHASE_ROW,
            L(672.5, (30, "01-Apr-2019"), (75, "onwards exit load is nil")),
            L(665.0, (75, "for all schemes")),
            REDEMPTION_ROW,
        ]
        txns = _txns(parse_lines(rows))
        assert len(txns) == 2
        assert txns[0].description == "Purchase - BSE -"

    def test_balance_restatement_row_emits_misc_with_balance(self, parse_lines):
        # Transmission/Transformation restatements print no ***, but do
        # print the running Unit Balance — emitted as MISC, and the
        # printed balance must reconcile cleanly.
        rows = [
            PURCHASE_ROW,
            L(672.5, (30, "02-Jan-2024"), (75, "Transformation In"), (R(550, "10.000"), "10.000")),
            REDEMPTION_ROW,
        ]
        data = parse_lines(rows)
        txns = _txns(data)
        assert len(txns) == 3
        assert txns[1].type == "MISC"
        assert txns[1].balance == Decimal("10.000")
        assert data.parse_warnings == []

    def test_distant_dateless_line_is_not_a_continuation(self, parse_lines):
        # Same cell shape, but ~34pt below the row (a stray footer):
        # rejected by the baseline-gap guard.
        rows = [PURCHASE_ROW, REDEMPTION_ROW, L(612, (75, "Page 1 of 2"))]
        txns = _txns(parse_lines(rows))
        assert txns[-1].description == "Redemption -"

    def test_tail_with_numeric_cell_is_not_merged(self, parse_lines):
        # Adjacent and dateless, but a glyph run lands in the Amount
        # zone — not a pure description wrap, so it is skipped.
        rows = [
            PURCHASE_ROW,
            L(672.5, (75, "stray fragment"), (R(360, "9.99"), "9.99")),
            REDEMPTION_ROW,
        ]
        txns = _txns(parse_lines(rows))
        assert txns[0].description == "Purchase - BSE -"

    def test_reclassified_type_stays_enum_and_serializes_clean(self, parse_lines):
        # Post-construction `txn.type` assignment bypasses pydantic
        # validation; assigning `.name` (a str) into the enum-typed field
        # trips PydanticSerializationUnexpectedValue on every JSON export
        # (issue #118 follow-up). The merge path must assign the enum.
        import warnings as _warnings

        from casparser.enums import TransactionType as TT

        rows = [PURCHASE_ROW, L(672.5, (75, "Instalment 5/18")), REDEMPTION_ROW]
        data = parse_lines(rows)
        for t in _txns(data):
            assert isinstance(t.type, TT), f"{t.description!r}: {type(t.type)}"
        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            data.model_dump_json()
        assert [str(w.message) for w in caught] == []

    def test_balances_still_reconcile(self, parse_lines):
        rows = [PURCHASE_ROW, L(672.5, (75, "Instalment 5/18")), REDEMPTION_ROW]
        data = parse_lines(rows)
        assert data.parse_warnings == []
        scheme = data.folios[0].schemes[0]
        assert scheme.close_calculated == Decimal("5.000")
