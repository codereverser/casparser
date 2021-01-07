# CASParser

[![code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![GitHub](https://img.shields.io/github/license/codereverser/casparser)](https://github.com/codereverser/casparser/blob/main/LICENSE)
![GitHub Workflow Status](https://img.shields.io/github/workflow/status/codereverser/casparser/run-tests)
[![codecov](https://codecov.io/gh/codereverser/casparser/branch/main/graph/badge.svg?token=DYZ7TXWRGI)](https://codecov.io/gh/codereverser/casparser)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/casparser)

Parse Consolidated Account Statement (CAS) PDF files generated from CAMS/KFINTECH


## Installation
```bash
pip install casparser
``` 

### with faster PyMuPDF parser
```bash
pip install casparser[mupdf]
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
                    "advisor": "string",
                    "rta_code": "string",
                    "rta": "string",
                    "open": "number",
                    "close": "number",
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
  - `TAX`
  - `MISC`
- `dividend_rate` is applicable only for `DIVIDEND_PAYOUT` and 
  `DIVIDEND_REINVESTMENT` transactions.
  
### CLI

casparser also comes with a command-line interface that prints summary of parsed 
portfolio in a wide variety of formats. 

```bash
Usage: casparser [-o output_file.json|output_file.csv] [-p password] [-s type] [-a] CAS_PDF_FILE

  -o, --output FILE               Output file path. Saves the parsed data as json or csv
                                  depending on the file extension. For other extensions, the
                                  summary output is saved. [See note below]

  -s, --summary simple|plain|grid|fancy_grid|html... 
                                  Print Summary of transactions parsed.
  -p PASSWORD                     CAS password
  -a, --include-all               Include schemes with zero valuation in the
                                  summary output

  --force-pdfminer                Force PDFMiner parser even if MuPDF is
                                  detected

  --version                       Show the version and exit.
  -h, --help                      Show this message and exit.
``` 

**Note:** `casparser cli` supports two special output file formats [-o _file.json_ / _file.csv_]
1. `json` - complete parsed data is exported in json format (including investor info)
2. `csv`  - transactions with AMC, Folio and Scheme info are exported into csv format. 
3. any other extension - The summary output is saved in the file. 

#### Demo

![demo](https://raw.githubusercontent.com/codereverser/casparser/main/assets/demo.jpg)


## License

CASParser is distributed under MIT license by default. However enabling the optional dependency
`mupdf` would imply the use of [PyMuPDF](https://github.com/pymupdf/PyMuPDF) /
[MuPDF](https://mupdf.com/license.html) and hence the licenses GNU GPL v3 and GNU Affero GPL v3 
would apply. Copies of all licenses have been included in this repository. - _IANAL_
 
## Resources
1. [CAS from CAMS](https://new.camsonline.com/Investors/Statements/Consolidated-Account-Statement)
2. [CAS from Karvy/Kfintech](https://mfs.kfintech.com/investor/General/ConsolidatedAccountStatement)

