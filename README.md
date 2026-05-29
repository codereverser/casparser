# CASParser

[![code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![GitHub](https://img.shields.io/github/license/codereverser/casparser)](https://github.com/codereverser/casparser/blob/main/LICENSE)
![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/codereverser/casparser/run-pytest.yml?branch=main)
[![codecov](https://codecov.io/gh/codereverser/casparser/branch/main/graph/badge.svg?token=DYZ7TXWRGI)](https://codecov.io/gh/codereverser/casparser)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/casparser)

Parse Consolidated Account Statement (CAS) PDF files generated from
CAMS, KFintech, NSDL, and CDSL.

`casparser` also includes a command line tool with the following analysis tools
- `summary`- print portfolio summary
- (**BETA**) `gains` - Print capital gains report (summary and detailed)
  - with option to generate csv files for ITR in schedule 112A format


## Supported inputs

`casparser` parses **original** CAS PDFs delivered by the four
recognised issuers:

| Issuer    | Variant(s)              | Source                              |
|-----------|-------------------------|-------------------------------------|
| CAMS      | Detailed, Summary       | `mailback.camsonline.com` request   |
| KFintech  | Detailed, Summary       | `mfs.kfintech.com` request          |
| NSDL      | Demat consolidated      | NSDL CAS email (monthly)            |
| CDSL      | Demat consolidated      | CDSL CAS email (monthly)            |

### Known unsupported inputs

- **Re-printed PDFs.** If you "print to PDF" an existing CAS
  (Microsoft Print to PDF, "Save as PDF" via a browser print
  dialog, macOS print preview → save, etc.) the watermark gets
  flattened from selectable text into a bitmap and the original
  generator metadata is wiped. The visual appearance is
  identical but `casparser` can no longer prove what it's
  looking at, and will reject the file. Re-request the
  statement from the issuer directly and parse the original.
- **MF Central statements.** MF Central's CAS uses a different
  template / generator and is not in scope for v1.0.
- **Third-party-reformatted statements** (broker portals that
  re-render CAS data, Excel/CSV exports converted back to PDF,
  etc.) — same reason as re-prints.

If you need to support one of these flows for downstream
tooling, the recommended path is to keep the original
issuer-delivered PDF alongside any redistributed copy and feed
the original to `casparser`.


## Installation
```bash
pip install -U casparser
```

Since v1.0 the parser is built on [pypdfium2](https://github.com/pypdfium2-team/pypdfium2)
(Apache-2.0 / BSD-3) — no optional PDF backends, no GPL/AGPL dependencies.


## Usage

```python
import casparser
data = casparser.read_cas_pdf("/path/to/cas/file.pdf", "password")

# Get data in json format
json_str = casparser.read_cas_pdf("/path/to/cas/file.pdf", "password", output="json")

# Get transactions data in csv string format
csv_str = casparser.read_cas_pdf("/path/to/cas/file.pdf", "password", output="csv")

```

### Data structure

```json
{
    "statement_period": {
        "from": "YYYY-MMM-DD",
        "to": "YYYY-MMM-DD"
    },
    "file_type": "CAMS/KFINTECH/NSDL/CDSL/UNKNOWN",
    "cas_type": "DETAILED/SUMMARY",
    "investor_info": {
        "email": "string",
        "name": "string",
        "mobile": "string",
        "address": "string"
    },
    "folios": [
        {
            "folio": "string",
            "amc": "string",
            "PAN": "string",
            "KYC": "OK/NOT OK",
            "PANKYC": "OK/NOT OK",
            "schemes": [
                {
                    "scheme": "string",
                    "isin": "string",
                    "amfi": "string",
                    "advisor": "string",
                    "rta_code": "string",
                    "rta": "string",
                    "type": "string",
                    "nominees": [
                      "string",
                    ],
                    "open": "number",
                    "close": "number",
                    "close_calculated": "number",
                    "valuation": {
                      "date": "date",
                      "nav": "number",
                      "value": "number",
                      "cost": "number",
                    },
                    "transactions": [
                        {
                            "date": "YYYY-MM-DD",
                            "description": "string",
                            "amount": "number",
                            "units": "number",
                            "nav": "number",
                            "balance": "number",
                            "type": "string",
                            "dividend_rate": "number"
                        }
                    ]
                }
            ]
        }
    ]
}
```
Notes:
- Transaction `type` can be any value from the following
  - `PURCHASE`
  - `PURCHASE_SIP`
  - `REDEMPTION`
  - `SWITCH_IN`
  - `SWITCH_IN_MERGER`
  - `SWITCH_OUT`
  - `SWITCH_OUT_MERGER`
  - `DIVIDEND_PAYOUT`
  - `DIVIDEND_REINVESTMENT`
  - `SEGREGATION`
  - `STAMP_DUTY_TAX`
  - `TDS_TAX`
  - `STT_TAX`
  - `MISC`
- `dividend_rate` is applicable only for `DIVIDEND_PAYOUT` and
  `DIVIDEND_REINVESTMENT` transactions.
- NSDL and CDSL statements return a different top-level shape with
  `accounts[].equities[]` and `accounts[].mutual_funds[]` instead of
  `folios[].schemes[]`. See `casparser.types.NSDLCASData` for details.

### CLI

casparser also comes with a command-line interface that prints summary of parsed
portfolio in a wide variety of formats.

```
Usage: casparser [-o output_file.json|output_file.csv] [-p password] [-s] [-a] CAS_PDF_FILE

  -o, --output FILE               Output file path. Saves the parsed data as json or csv
                                  depending on the file extension. For other extensions, the
                                  summary output is saved. [See note below]

  -s, --summary                   Print Summary of transactions parsed.
  -p PASSWORD                     CAS password
  -a, --include-all               Include schemes with zero valuation in the
                                  summary output
  -g, --gains                     Generate Capital Gains Report (BETA)
  --gains-112a ask|FY2020-21      Generate Capital Gains Report - 112A format for
                                  a given financial year - Use 'ask' for a prompt
                                  from available options (BETA)

  --version                       Show the version and exit.
  -h, --help                      Show this message and exit.
```

#### CLI examples
```
# Print portfolio summary
casparser /path/to/cas.pdf -p password

# Print portfolio and capital gains summary
casparser /path/to/cas.pdf -p password -g

# Save parsed data as a json file
casparser /path/to/cas.pdf -p password -o pdf_parsed.json

# Save parsed data as a csv file
casparser /path/to/cas.pdf -p password -o pdf_parsed.csv

# Save capital gains transactions in csv files (pdf_parsed-gains-summary.csv and
# pdf_parsed-gains-detailed.csv)
casparser /path/to/cas.pdf -p password -g -o pdf_parsed.csv

```

**Note:** `casparser cli` supports two special output file formats [-o _file.json_ / _file.csv_]
1. `json` - complete parsed data is exported in json format (including investor info)
2. `csv` - Summary info is exported in csv format if the input file is a summary statement or if
   a summary flag (`-s/--summary`) is passed as argument to the CLI. Otherwise, full
   transaction history is included in the export.
   If `-g` flag is present, two additional files '{basename}-gains-summary.csv',
   '{basename}-gains-detailed.csv' are created with the capital-gains data.
3. any other extension - The summary table is saved in the file.


#### Demo

![demo](https://raw.githubusercontent.com/codereverser/casparser/main/assets/demo.jpg)

## ISIN & AMFI code support

Since v0.4.3, `casparser` includes support for identifying ISIN and AMFI code for the parsed schemes
via the helper module [casparser-isin](https://github.com/codereverser/casparser-isin/). If the parser
fails to assign ISIN or AMFI codes to a scheme, try updating the local ISIN database by

```shell
casparser-isin --update
```

If it still fails, please raise an issue at [casparser-isin](https://github.com/codereverser/casparser-isin/issues/new) with the
failing scheme name(s).

## License

CASParser is distributed under the MIT license. Up to v0.8 the optional
`mupdf` / `fast` extra pulled in [PyMuPDF](https://github.com/pymupdf/PyMuPDF) /
[MuPDF](https://mupdf.com/license.html), which would have caused GNU GPL v3
and GNU Affero GPL v3 to apply transitively. v1.0 dropped that extra
(the PyMuPDF and pdfminer.six backends are gone; the parser now runs on
[pypdfium2](https://github.com/pypdfium2-team/pypdfium2), which is dual
Apache-2.0 / BSD-3), so casparser is now pure MIT end-to-end.

## Resources
1. [CAS from CAMS](https://www.camsonline.com/Investors/Statements/Consolidated-Account-Statement)
2. [CAS from Karvy/Kfintech](https://mfs.kfintech.com/investor/General/ConsolidatedAccountStatement)
3. [NSDL Consolidated Account Statement](https://nsdlcas.nsdl.com/)
4. [CDSL Consolidated Account Statement](https://www.cdslindia.com/Investors/Cas.html)
