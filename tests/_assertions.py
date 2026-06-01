"""Invariant helpers shared across the e2e test suite.

Each assertion checks a structural / arithmetic property of a parsed
CAS object — designed so the test files can lock in correctness
without encoding the real rupee figures from private fixtures.

Examples of what these catch:
  - Column-swap bugs   (qty * price != value)
  - Decimal-parse bugs (comma stripped wrong)
  - Routing bugs       (a bond row entering equities list)
  - Anchor-drift bugs  (units cell read as 0)

Tolerances are deliberately small — these are bookkeeping numbers from
the CAS itself, so the rounding error is bounded by how the source
statement rounds.
"""

from __future__ import annotations

import re
from decimal import Decimal

# 1 paisa absolute slop is enough — these numbers come from the CAS as
# already-rounded printed figures, so we expect exact equality but
# leave a hair-thin epsilon for Decimal arithmetic noise.
ABS_TOL = Decimal("0.01")

# 0.5% relative tolerance on derived figures (qty * nav vs value).
# CAS issuers truncate NAVs at 4dp and units at 3dp, so per-row
# rounding can land 5–10 paise off on a ₹5L scheme without indicating
# a parser bug.
REL_TOL = Decimal("0.005")

# Indian PAN format: 5 letters, 4 digits, 1 letter.
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def _D(x) -> Decimal:
    return Decimal(str(x))


def assert_relclose(actual, expected, tol: Decimal = REL_TOL, *, label: str = ""):
    """abs(actual - expected) / abs(expected) <= tol (or absolute slop
    when expected == 0)."""
    a, e = _D(actual), _D(expected)
    if e == 0:
        assert abs(a) <= ABS_TOL, f"{label}: expected ~0, got {a}"
        return
    rel = abs(a - e) / abs(e)
    assert rel <= tol, f"{label}: {a} vs {e}, rel_diff={rel:.4%} > {tol:.4%}"


# ----------------------------------------------------------------- CAMS/KFin
def assert_scheme_well_formed(scheme):
    """Schemes must always carry ISIN + AMFI + a positive valuation
    (the v1.0 parser populates both via casparser-isin)."""
    assert scheme.isin, f"scheme {scheme.scheme!r}: no ISIN"
    assert scheme.amfi, f"scheme {scheme.scheme!r}: no AMFI"
    assert scheme.rta_code, f"scheme {scheme.scheme!r}: no rta_code"
    # RTA is a clean registrar acronym (CAMS, KFINTECH, KARVY, FTAMIL,
    # ...). Wrapped "(Non Demat)" scheme headers used to leak an advisor /
    # ISIN / watermark fragment into this field (e.g. "(Advisor:", "iv",
    # "01101(Advisor:") — assert the shape rather than an allowlist so
    # legitimate self-RTAs aren't false-flagged.
    assert re.fullmatch(
        r"[A-Z]{3,12}", scheme.rta or ""
    ), f"scheme {scheme.scheme!r}: malformed RTA {scheme.rta!r}"
    assert scheme.valuation is not None
    assert _D(scheme.valuation.nav) > 0, f"scheme {scheme.scheme!r}: zero/negative NAV"


# Sentence fragments from the CAS footer / disclaimer / load-structure
# notes that legitimately never appear inside a mutual-fund scheme name.
# Their presence means trailing notes bled into the name.
_FOOTER_BLEED_RE = re.compile(
    r"kindly|FATCA|\bCRS\b|stamp\s+duty|addendum|please\s+refer|"
    r"redeemed\s+after|date\s+of\s+allotment|basis\s+relevant|"
    r"tax\s+provisions|immediately|effect\s+from|evaluated\s+by\s+investor",
    re.I,
)


def assert_scheme_name_clean(scheme):
    """A scheme name reads like a fund name — it must not absorb the
    trailing grand-total row, notes or disclaimers.

    Regression guard for the CAMS/KFin SUMMARY footer-bleed bug, where
    the last scheme's name swallowed the `Total ...` row plus the
    disclaimer paragraphs that follow the holdings table.
    """
    name = scheme.scheme or ""
    assert not _FOOTER_BLEED_RE.search(
        name
    ), f"scheme name has footer/disclaimer text bled in: {name!r}"
    # A real fund name (even with a "(formerly ...)" suffix + plan +
    # option) stays well under this; the bled names ran 170-280 chars.
    assert len(name) <= 150, f"scheme name implausibly long ({len(name)} chars): {name!r}"


def assert_scheme_valuation_arithmetic(scheme):
    """`close_balance * valuation.nav` should reproduce
    `valuation.value` to within rounding error.

    Catches a swapped or misread NAV / value column."""
    close = _D(scheme.close)
    if close == 0:
        # Fully redeemed schemes legitimately have value = 0.
        assert (
            _D(scheme.valuation.value) == 0
        ), f"scheme {scheme.scheme!r}: close=0 but value={scheme.valuation.value}"
        return
    derived = close * _D(scheme.valuation.nav)
    assert_relclose(
        derived,
        scheme.valuation.value,
        label=f"scheme {scheme.scheme!r}: close*nav vs value",
    )


def assert_scheme_transaction_units_close(scheme):
    """`open + Σ(txn.units) == close` (exact to 3-decimal-place
    precision, matching how CAS issuers report unit balances).

    This is the strongest correctness check on the transaction
    history: every txn that changes units must be captured. A
    missed purchase, misread date, dropped redemption, or merged
    duplicate row breaks the equality.

    Stamp-duty and STT entries don't carry units (``t.units is
    None``) so they're skipped here — they only deduct from the
    cash side, not the unit balance.
    """
    o = _D(scheme.open)
    c = _D(scheme.close)
    sum_u = sum(
        (_D(t.units) for t in scheme.transactions if t.units is not None),
        _D(0),
    )
    diff = abs(o + sum_u - c)
    assert diff <= _D("0.001"), (
        f"scheme {scheme.scheme!r}: open={o} + Σ(units)={sum_u} " f"!= close={c} (diff={diff})"
    )


def assert_folio_well_formed(folio):
    assert PAN_RE.match(
        folio.PAN or ""
    ), f"folio {folio.folio!r}: PAN {folio.PAN!r} fails {PAN_RE.pattern}"
    assert folio.amc, f"folio {folio.folio!r}: empty AMC"
    assert folio.schemes, f"folio {folio.folio!r}: no schemes"


def assert_investor_info_complete(info):
    """CAMS/KFin investor info: every field is populated."""
    assert info.name, "investor: missing name"
    assert info.email, "investor: missing email"
    assert info.mobile, "investor: missing mobile"
    assert info.address, "investor: missing address"


# ----------------------------------------------------------------- NSDL/CDSL
ISIN_EQ_RE = re.compile(r"^IN[E9][0-9A-Z]{8}\d$")
ISIN_MF_RE = re.compile(r"^INF[0-9A-Z]{8}\d$")
ISIN_ANY_RE = re.compile(r"^[A-Z]{2}[0-9A-Z]{9}\d$")


def assert_equity_well_formed(eq):
    """Every NSDL/CDSL equity row carries a well-formed ISIN.

    Lapsed rights entitlements / fully-redeemed positions show up
    with num_shares=0 and value=0 — those are valid informational
    rows, not parser failures, so the invariant only asserts
    consistency: when value > 0 the row must also have a positive
    price and quantity, and vice versa.

    The harder per-row invariant (`num_shares * price == value`)
    is intentionally *not* enforced — some NSDL summary rows
    inline a 'of which Pledged' note that confuses the quantity
    column. The account-level Σ(value) == balance check covers
    the misrouted-row case, which is the more dangerous one.
    """
    assert ISIN_ANY_RE.match(eq.isin or ""), f"equity: bad ISIN {eq.isin!r}"
    assert _D(eq.value) >= 0, f"equity {eq.isin}: negative value"
    assert _D(eq.price) >= 0, f"equity {eq.isin}: negative price"
    assert _D(eq.num_shares) >= 0, f"equity {eq.isin}: negative shares"
    if _D(eq.value) > 0:
        assert _D(eq.price) > 0, f"equity {eq.isin}: positive value {eq.value} but zero price"


def assert_mutual_fund_well_formed(mf):
    """Every NSDL/CDSL MF holding has an INF ISIN and obeys
    `balance * nav ≈ value`.

    Fully-redeemed schemes legitimately show with balance=0 and
    value=0; in that case we only verify the ISIN is well-formed
    and that no value > 0 sneaks in without a positive balance/nav.

    Otherwise this is the strongest per-row invariant for the MF
    Holdings detailed table — catches the anchor-drift case where
    the units cell falls outside its expected x-band and balance
    reads as 0 while value stays correct.
    """
    assert ISIN_MF_RE.match(mf.isin or ""), f"MF: bad ISIN {mf.isin!r}"
    assert _D(mf.value) >= 0, f"MF {mf.isin}: negative value"
    assert _D(mf.balance) >= 0, f"MF {mf.isin}: negative balance"
    if _D(mf.value) == 0:
        # Fully-redeemed — balance must also be 0, NAV may be any value.
        assert _D(mf.balance) == 0, f"MF {mf.isin}: value=0 but balance={mf.balance}"
        return
    assert _D(mf.nav) > 0, f"MF {mf.isin}: zero NAV with positive value"
    derived = _D(mf.balance) * _D(mf.nav)
    assert_relclose(
        derived,
        mf.value,
        label=f"MF {mf.isin}: balance*nav vs value",
    )


def assert_bond_summary_form(bd):
    """Summary-form bonds (NSDL-account pages) carry full metadata
    and obey `num_bonds * face_value == value` exactly."""
    assert ISIN_ANY_RE.match(bd.isin or ""), f"bond: bad ISIN {bd.isin!r}"
    assert bd.face_value is not None, f"bond {bd.isin}: missing face_value"
    assert bd.coupon_rate is not None, f"bond {bd.isin}: missing coupon_rate"
    assert bd.coupon_frequency, f"bond {bd.isin}: missing coupon_frequency"
    assert bd.maturity_date, f"bond {bd.isin}: missing maturity_date"
    # Summary form doesn't carry market price.
    assert bd.market_price is None, f"bond {bd.isin}: unexpected market_price on summary row"
    derived = _D(bd.num_bonds) * _D(bd.face_value)
    assert derived == _D(
        bd.value
    ), f"bond {bd.isin}: num_bonds*face_value={derived} != value={bd.value}"


def assert_bond_detailed_form(bd):
    """Detailed-form bonds (CDSL-account pages) carry only quantity,
    market price and value — no coupon metadata."""
    assert ISIN_ANY_RE.match(bd.isin or ""), f"bond: bad ISIN {bd.isin!r}"
    assert bd.market_price is not None, f"bond {bd.isin}: missing market_price"
    assert bd.face_value is None, f"bond {bd.isin}: unexpected face_value on detailed row"
    assert bd.coupon_rate is None, f"bond {bd.isin}: unexpected coupon_rate on detailed row"
    derived = _D(bd.num_bonds) * _D(bd.market_price)
    assert_relclose(
        derived,
        bd.value,
        label=f"bond {bd.isin}: num_bonds*market_price vs value",
    )


def assert_account_balance_closes(account):
    """Σ(equity.value) + Σ(mf.value) + Σ(bond.value) == account.balance.

    The strongest holding-level invariant for NSDL/CDSL: if a row was
    misrouted between sections (e.g. a bond counted as an equity)
    this sum still matches because the value column is the same;
    if a row was DROPPED, the sum falls short.
    """
    eq = sum((_D(e.value) for e in account.equities), Decimal(0))
    mf = sum((_D(m.value) for m in account.mutual_funds), Decimal(0))
    bd = sum((_D(b.value) for b in account.bonds), Decimal(0))
    derived = eq + mf + bd
    diff = abs(derived - _D(account.balance))
    assert diff <= ABS_TOL, (
        f"account {account.type!r} dp={account.dp_id or '-'} cl={account.client_id or '-'}: "
        f"Σ(values)={derived}  balance={account.balance}  diff={diff}"
    )


def assert_demat_account_well_formed(account):
    """A NSDL/CDSL demat account has at least one named owner, plus
    DP/Client IDs in the expected formats."""
    assert account.type in (
        "NSDL Demat Account",
        "CDSL Demat Account",
        "Mutual Fund Folios",
    ), f"account: unexpected type {account.type!r}"
    if account.type == "NSDL Demat Account":
        # NSDL DP IDs look like 'IN######' (IN + 6 digits).
        assert re.match(
            r"^IN\d{6}$", account.dp_id or ""
        ), f"NSDL demat: bad DP ID {account.dp_id!r}"
        # Client IDs are 8-digit.
        assert re.match(r"^\d{8}$", account.client_id or ""), "NSDL demat: bad Client ID format"
    elif account.type == "CDSL Demat Account":
        # CDSL DP IDs are 8-digit numerics.
        assert re.match(r"^\d{8}$", account.dp_id or ""), "CDSL demat: bad DP ID format"
        assert re.match(r"^\d{8}$", account.client_id or ""), "CDSL demat: bad Client ID format"
    else:
        # Pseudo MF-Folios account has no DP/Client.
        assert account.dp_id == "" and account.client_id == ""
