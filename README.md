# CASParser

[![code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![GitHub](https://img.shields.io/github/license/codereverser/casparser)](https://github.com/codereverser/casparser/blob/main/LICENSE)
![GitHub Workflow Status](https://img.shields.io/github/workflow/status/codereverser/casparser/run-tests)
[![codecov](https://codecov.io/gh/codereverser/casparser/branch/main/graph/badge.svg?token=DYZ7TXWRGI)](https://codecov.io/gh/codereverser/casparser)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/casparser)

Parse Consolidated Account Statement (CAS) PDF files generated from CAMS/KFINTECH

`casparser` also includes a command line tool with the following analysis tools
- `summary`- print portfolio summary
- `gains`- Print capital gains report (summary and detailed) 


## Installation
```bash
pip install -U casparser
``` 

### with faster PyMuPDF parser
```bash
pip install -U 'casparser[mupdf]'
```

**Note:** Enabling this dependency could result in licensing changes. Check the 
[License](#license) section for more details 
 

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
    "file_type": "CAMS/KARVY/UNKNOWN",
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
                    "open": "number",
                    "close": "number",
                    "close_calculated": "number",
                    "valuation": {
                      "date": "date",
                      "nav": "number",
                      "value": "number"
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
  - `STT_TAX`
  - `MISC`
- `dividend_rate` is applicable only for `DIVIDEND_PAYOUT` and 
  `DIVIDEND_REINVESTMENT` transactions.
  
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
  -g, --gains                     Generate Capital Gains Report (BETA) [Debt fund indexation not 
                                  considered]
  --force-pdfminer                Force PDFMiner parser even if MuPDF is
                                  detected

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

CASParser is distributed under MIT license by default. However enabling the optional dependency
`mupdf` would imply the use of [PyMuPDF](https://github.com/pymupdf/PyMuPDF) /
[MuPDF](https://mupdf.com/license.html) and hence the licenses GNU GPL v3 and GNU Affero GPL v3 
would apply. Copies of all licenses have been included in this repository. - _IANAL_
 
## Resources
1. [CAS from CAMS](https://new.camsonline.com/Investors/Statements/Consolidated-Account-Statement)
2. [CAS from Karvy/Kfintech](https://mfs.kfintech.com/investor/General/ConsolidatedAccountStatement)

