# Changelog

## 1.2.1

### Fixed

- Fix mis-classification of STP (Systematic Transfer Plan) for
  Kfintech-serviced schemes. (discussion #141)

## 1.2.0

### New

- **Gift (inter-folio transfer) transactions.** Adds `GIFT_IN` / `GIFT_OUT`
  transaction types, classified on the `"gift"` keyword together with the units
  sign. Closes #134.
- **`TransactionData.gift_folio`.** Carries the counterparty folio named in a
  gift row's description, so a donor's statement can be linked to the donee's
  across two CAS files. `None` for all other transaction types.

### Fixed

- Gift row mistaken for a folio boundary.

## 1.1.0

### New

- **`CASData.parse_warnings`.** The CAMS/KFin DETAILED parser now reconciles each
  scheme's transactions against its printed running `Unit Balance` column — the
  statement's own checksum — and records a non-fatal warning on any
  discontinuity (`prev_balance + units != printed balance`) or closing-balance
  mismatch. A non-empty list means a transaction row was likely dropped or
  mis-parsed; the parse still returns, but that scheme's data should not be
  trusted blindly. The region-based header parser additionally warns when a
  scheme-header region is discarded without its `Opening Unit Balance` anchor
  (folio/AMC boundary, end of document, or an unparseable header) — previously
  such a scheme and all its transactions vanished silently. Warnings are printed
  by the CLI after a successful parse.
- **JSON Schemas for the output models.** `schema/CASData.schema.json` and
  `schema/NSDLCASData.schema.json` are generated from the Pydantic models
  (`scripts/generate_schema.py`); the README shape documentation was reworked
  around them.

### Fixed

- **Dividend reinvest misclassification.** `DIVIDEND_RE` carried an inline
  `(reinvest)*` group inside two lazy quantifiers that never backtracked to
  capture, so `"Reinvestment of IDCW @ Rs…"` and `"IDCW - Reinvest @ Rs…"` were
  classified as `DIVIDEND_PAYOUT`. Reinvest is now detected with a separate
  substring search.
- **Folio collisions across AMCs.** Folios were keyed by folio number alone, but
  folio numbers are RTA-scoped, not globally unique — two AMCs sharing one
  silently merged the second AMC's schemes into the first's folio. Now keyed by
  `(amc, folio_no)`.
- **Advisor-wrap header mis-parsed as a bogus `ARN` scheme.** A wrapped
  `(Advisor: ARN-xxxxx)` continuation line could anchor a spurious scheme whose
  RTA code parsed as `ARN`, splitting one holding into an empty stub plus a
  mis-coded duplicate.

### Changed

- **Scheme-header parsing rewritten as anchor-delimited regions.** The CAMS/KFin
  DETAILED parser no longer stitches headers by line-local proximity
  (`Y_BAND` / `consumed_below` lookahead). It accumulates the lines between the
  folio line (or the previous scheme's footer) and the next `Opening Unit
  Balance` into one region and parses fields from it once — bounded by the
  document's reliable single-line anchors, so it tolerates arbitrary header wrap
  distance and stitches headers across page breaks (which the old code could
  not). Output is compatible with 1.0.1; the only changes on the private corpus
  are cleaner scheme names (interleaved-ISIN IDCW names keep their
  `Payout` / `Reinvest` qualifier; leaked advisor / ISIN fragments are removed).

## 1.0.1

### New

- **Demat equity holdings now carry `symbol` + `exchange`.** NSDL/CDSL
  statements identify an equity by ISIN only, so `Equity` rows used to lack an
  exchange trading ticker. The NSE symbol + exchange are now backfilled from the
  ISIN database after parsing (`parsers._isin.batch_equity_symbols`,
  `_enrich_demat_equities`), mirroring the existing MF `amfi` / `type`
  enrichment — so a demat holding identified only by ISIN can be priced via a
  symbol-keyed feed. Unresolvable ISINs stay `None`. Requires
  `casparser-isin>=2026.6.9` (ships the symbol columns + `ISINDb.batch_isin_lookup`).

## 1.0.0

Major release. The parsing backend was rewritten from scratch on
[pypdfium2](https://github.com/pypdfium2-team/pypdfium2) (Apache-2.0 /
BSD-3) and the four supported CAS issuers now each have a dedicated
parser tuned to their template family.

### Breaking changes

- **pdfminer.six and PyMuPDF backends removed.** `casparser.read_cas_pdf`
  no longer dispatches between them. The `mupdf` / `fast` extras in
  `pyproject.toml` are gone. The `--force-pdfminer` CLI flag and the
  `force_pdfminer=` kwarg on `read_cas_pdf` are kept as no-ops; the
  kwarg emits a `DeprecationWarning` and is otherwise ignored.
- **License simplified to pure MIT.** With the GPL/AGPL-licensed
  PyMuPDF dependency gone, the `licenses/` directory of GPL/AGPL
  copies has been removed. pypdfium2 is dual Apache-2.0 / BSD-3 and
  doesn't impose any copyleft obligation on users of casparser.
- **Minimum Python is now 3.11.** 3.9 / 3.10 classifiers dropped from
  `pyproject.toml`.
- **`CASData.investor_info` is now `Optional[InvestorInfo]`** (matches
  the `NSDLCASData.investor_info` shape that already existed). It is
  populated on every supported issuer, but consumers should still
  guard against the `None` case for unfamiliar templates.
- **Internal `casparser.process` package removed.** The two helpers
  downstream code still imports from it are now at
  `casparser.parsers._classify` (`get_parsed_scheme_name`,
  `get_transaction_type`) and `casparser.parsers._isin` (`isin_search`).

### New

- **Demat MF holdings now carry `amfi` + `type`.** NSDL/CDSL statements
  identify a mutual-fund holding by ISIN only, so `MutualFund` rows used
  to lack the AMFI code and scheme type that RTA (CAMS/KFin) `Scheme`
  rows have. Both are now backfilled from the ISIN database in a single
  batched lookup after parsing (`parsers._isin.batch_isin_metadata`), so
  the same fund parsed from a depository CAS and an RTA CAS reconciles on
  `amfi` / `type` when imported together. Unresolvable ISINs stay `None`.
- **First-class NSDL and CDSL parsers.** Drops the regex-on-text
  approach the 0.8 NSDL/CDSL code used; the new parsers consume
  structured `Block`/`Cell` records directly from `pypdfium2`. Several
  bugs the v0.8 NSDL/CDSL code shipped with are no longer in scope
  (misplaced-UCC-as-folio on NSDL MF Holdings, space-merged
  folio+units cells on CDSL, the silently-dropped NSDL HDFC
  subaccount on CDSL multi-account statements, `Optional[Decimal]`
  comma-strip miss in the `MutualFund` validator).
- **CAMS / KFin 2026 templates supported** out of the box. The newer
  CAMS SUMMARY template added an ISIN column the v0.8 regex didn't
  match; v1.0 parses all rows. The newer KFin SUMMARY template emits
  zero-balance schemes with single-space-separated trio cells that
  the v0.8 regex required `\t\t` between; v1.0 picks them up too.
- **AMC-header detection extended** to include the `Fund House`
  suffix. v0.8's regex only matched `Mutual Fund` / `MF` suffixes,
  so schemes from a few newer AMCs whose names end in `Fund House`
  ended up bucketed under the previous AMC.
- **ISIN / AMFI enrichment has a direct-ISIN fallback** path via
  `MFISINDb.direct_isin_lookup` for the case where multi-line
  `Registrar:` rendering corrupts the RTA token.
- **Schedule 112A column 1b** ("Share/Unit Transferred") is emitted
  for FY2024-25 onward, per the AY 2025-26 ITR utility template. The
  Finance (No. 2) Act 2024 split the equity-LTCG regime on
  23-Jul-2024; the 112A CSV now flags each transfer `BE`/`AE` against
  that date and splits an after-31-Jan-2018-acquired fund into
  separate rows when it was sold on both sides of the cutoff. Older
  FYs keep the 14-column layout their utility expects.
- **Cost Inflation Index extended to FY2025-26 (376)** and the
  FY2024-25 value corrected from `365` to the CBDT-notified `363`
  (the wrong value slightly mis-indexed debt-fund LTCG cost of
  acquisition for FY2024-25 sales).

### Fixed

- **CAMS SUMMARY `valuation.date` no longer mis-parses to year 201**
  (was a column-boundary bug — the NAVDate column treated as
  right-aligned with a 42pt width clipped the trailing year digit,
  then Pydantic mis-coerced the `01-Jan-201` string).
- **CDSL multi-account statements** (5+ demat accounts on one PDF) are
  now parsed correctly. Earlier the page-3+ scan only kicked in from
  page 8, dropping holdings sections that landed on pages 4-7.
- **CDSL MF holdings** rows with `DIRECT` (or any non-`ARN-XXXX`
  distribution-mode token) now correctly populate `pnl` and `return_`.
- **Leading-dot decimals** (`.196`, `-.5`) are now recognised as
  numeric by the NSDL / CDSL cell classifier. CDSL occasionally
  drops the leading zero on sub-unit balances; under the old regex
  those cells were mis-bucketed as text, shifting the row layout
  and producing a silent `Σholdings ≠ balance` mismatch.
- **KFin `... - Reversed` transaction rows** (e.g. the Franklin
  wound-up debt schemes' `Payment - Units Extinguished-Reversed`
  entries) had their units cell printed with cosmetic parentheses
  even though the semantic sign is the opposite of the original.
  A new running-balance cross-check in the CAMS / KFin DETAILED
  parser flips the sign (and matching amount) on any single-row
  mis-parse and reclassifies the transaction type via the
  positive-units branch of `get_transaction_type`. Acts as a
  self-validating safety net for the entire transaction stream,
  not just the Franklin case.
- **Failed-SIP `Payment not received` rows** are now classified as
  `REVERSAL` instead of `REDEMPTION`. These carry negative units (the
  provisional purchase is being undone) with a correct sign, so the
  running-balance validator leaves them untouched — only the
  description keyword distinguishes them, and it was missing from the
  reversal pattern. Misclassifying them as redemptions injected a
  phantom capital-gains event into the gains analysis.
- **Stamp-duty allocation on split lots.** When a single purchase
  lot is consumed across N disposals the `FIFOUnits.sell` re-queue
  carried the *full* original stamp duty on every remaining slice,
  so the proportional allocation re-claimed the same paid stamp
  on each disposal — total stamp claimed grew unboundedly with
  split depth. (Worked example: 300-unit lot with ₹1.25 stamp,
  split 100/100/100 → total claimed ₹2.29 vs ₹1.25 paid, an 84%
  over-claim that further widens for deeper splits.) The lot is
  now re-queued with the *unallocated remainder* (`purchase_tax -
  stamp_duty`), so the invariant `Σ(stamp_claimed) == stamp_paid`
  holds exactly across any number of partial consumptions. Section
  48 only permits deducting the stamp actually paid as a
  transfer expense, so the prior behaviour over-stated the
  deduction on Schedule 112A.
- **Soft-hyphen-wrapped tokens in NSDL / CDSL holdings.** Some CAS
  generators insert a `U+00AD` soft hyphen at the point where a long
  token (notably a 12-char ISIN) wraps across two display lines, e.g.
  `INF179K01<soft-hyphen>WN9`. The block/cell extractor now treats a trailing
  soft hyphen as a continuation marker (splicing the next fragment on
  with no separator) and strips any embedded soft hyphens, so the
  token is reconstructed intact. Previously the wrapped ISIN matched
  neither the anchored ISIN regex nor its leftover, and the holding
  was silently dropped. (Reconstruction works when the wrapped
  fragments fall in the same row block; ISINs split across blocks by
  a non-standard page scale are not covered.)

## 0.9.1 - 2026-06-02
- CDSL: parse holdings rows whose ISIN spans/wraps across multiple
  lines instead of silently dropping them (#129, thanks @mvineetmenon).
- CDSL: parse stock holdings rows where a long security name wraps so
  the line no longer begins with the ISIN — these equities were
  previously skipped entirely (#132 / #133, thanks @mvineetmenon).
- Classify failed-SIP `Payment not received` transactions as
  `REVERSAL` rather than `REDEMPTION`, so a reversed (unsettled)
  purchase no longer surfaces as a spurious redemption / capital-gains
  event (#131, thanks @rahulpatel135).
- Allow `pytest` 9.x in the dev/test dependency range (#126).

## 0.9.0 - 2026-05-22
- Add support for CDSL statements
- Drop support for Python 3.9 and 3.10; minimum supported version is now 3.11
- Support PyMuPDF >= 1.25 (1.27.x tested). Older `<1.25` pin removed.
- Bump `casparser-isin` to `>= 2026.5.1` (new DB format v2 with
  `sebi_category`/`last_seen` columns; ISIN-first lookup priority).
- Relax other dependency pins (click, colorama, rich, pdfminer.six).
- Fix `MutualFund.fix_float` pydantic validator so the aliased `return`
  field (Python attribute `return_`) also gets the comma-stripping
  treatment; previously NSDL MF folio rows with a return value of
  1 lakh or more would fail Decimal validation.
- Parser robustness fixes for PyMuPDF 1.25+ text extraction quirks
  (all superseded in 1.0.0 by the pypdfium2 rewrite, kept here for
  the historical record).

## 0.8.1 - 2025-09-21
- NSDL parser bug fixes

## 0.8.0 - 2025-02-26
- NSDL support: first version
- various bug fixes

## 0.7.4 - 2023-10-04
- (fix) fix broken scheme names

## 0.7.3 - 2023-09-26
- (new) Add support for parsing nominee details (available in `Folio.nominees`)
- (fix) fix empty PAN in certain cases

## 0.7.2 - 2023-09-19
- Bug fixes
  - Exclude short term capital gains (STCG) from 112A reports
  - Fix advisor code parsing

# 0.7.1 - 2023-09-06
- fix bug where long scheme names were getting truncated

## 0.7.0 - 2023-09-03
- update pydantic to v2

## 0.6.1 - 2023-04-04
- update CII data till FY2022-23
- bug fixes

## 0.6.0 - 2023-02-20
- use pydantic models for better data validation

## 0.5.5 - 2022-08-06
- bug fix with MuPDF parser

## 0.5.4 - 2022-02-01
- bug fix in CAS summary statement parser

## 0.5.3 - 2021-08-21
- support for generating csv files for capital gains in 112A format for income tax filing
- various bug fixes

## 0.5.2 - 2021-08-11
- fix crash while generating capital gains reports on dividend payout funds
- rework capital gains algorithm
- add `advisor` column in transactions csv
- various bug fixes

## 0.5.1 - 2021-07-21
- gains: PnL report
- support for migration of Franklin Templeton funds to CAMS RTA
- various bug fixes

## 0.5.0 - 2021-07-02
- Support for calculating capital gains from detailed CAS statements
- support for parsing Tax Deducted at Source (`TDS`) transactions

## 0.4.8 - 2021-06-27
- `REVERSAL` TransactionType to indicate reverted/rejected transactions
- convert all enums to strEnums for better readability [(#35)](https://github.com/codereverser/casparser/pull/35)
- fix issue with parsing multi-line transactions

## 0.4.7 - 2021-06-01

- Minor bug fixes in summary-statement parser.
- cli now uses [rich](http://rich.readthedocs.io/) for console output.
- Use poetry for dependency management and deployment.
- **BREAKING CHANGE**: Table output choices have been removed.
  `-s/--summary` is a flag and doesn't accept any additional arguments.
- Support for folios without PAN (#28).
- add support for new style dividend transactions after IDCW renaming.
- improved parser for transaction entries.

## 0.4.6 - 2021-04-04

- New `sort_transactions` option in `casparser.read_cas_pdf` (and `--sort` flag in CLI)
  to fix transactions (and balances) for schemes with non-chronological order.
- support negative balances in transactions

## 0.4.5 - 2021-03-20

- Support for segregated portfolio transactions [ credits: [abhishekjain-qb](https://github.com/abhishekjain-qb) ]

## 0.4.4 - 2021-02-07

- CSV output fixes
  - better csv output format for summary CAS statements.
  - option to output only summary information for detailed statements
    (`-s fancy_grid -o output.csv`)

## 0.4.3 - 2021-01-30

- ISIN, AMFI code mapping for schemes (**beta**)

## 0.4.2 - 2021-01-22

- fixes unicode issues in windows
- supports negative scheme balances
- better scheme name parsing
- fixes dividend transaction parsing


## 0.4.1 - 2021-01-13

- hotfix for parsing folios without KYC details

## 0.4.0 - 2021-01-08

- adds support for parsing summary statements from CAMS/KARVY (**beta**)
- minor bug fixes in CSV file generation

## 0.3.9 - 2021-01-01

- Support for classifying  transactions

## 0.3.8 - 2020-12-29

- Support for parsing folios without PAN/KYC details

## 0.3.7 - 2020-12-24

- Support for parsing dividend transactions

## 0.3.6 - 2020-12-21

- Support for parsing folios without advisor

## 0.3.5 - 2020-11-13

- Support for parsing scheme's latest nav
- Replaced `texttable` with `tabulate` for more cli output formats
- Added more test cases

## 0.3.4 - 2020-11-08

- Support for parsing scheme valuation
- Parser code refactor (transparent to user)

## 0.3.3 - 2020-10-31

- Added `balance` to Transaction
- Added test cases with travis-ci and codecov support
- minor bug fixes while parsing kfintech cas files

## 0.3.2 - 2020-10-29

- minor bug fixes

## 0.3.1 - 2020-10-26

- re-release 0.3.0 : Minor bug fix

## 0.3.0 - 2020-10-25

- **Breaking Change**: In order to preserve the order of entries, during format
  conversion to other data types like json., `folios` is a list instead of dict.
- Added a second parser based on [PyMuPDF](https://github.com/pymupdf/PyMuPDF) /
[MuPDF](https://mupdf.com/) - ~15-20x faster compared to pure-python pdfminer.
- Added AMC detection (accessible via `amc` property of folio)
- CLI summary now includes the number of transactions processed.

## 0.2.1 - 2020-10-23

- `read_cas_pdf` now supports more input types
- better cli summary output
- fixed investor info parsing where mobile numbers don't have country code
- updated dependencies

## 0.2.0 - 2020-10-15

- removed support for python versions <3.8
- Better investor info parser

## 0.1.2 - 2020-10-14

- Support for parsing investor info

## 0.1.1 - 2020-10-14

- Support for parsing folios without PAN

## 0.1.0 - 2020-10-11

- Initial release
