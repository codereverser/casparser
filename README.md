# CASParser

[![code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![GitHub](https://img.shields.io/github/license/codereverser/casparser)](https://github.com/codereverser/casparser/blob/main/LICENSE)
[![Travis CI](https://img.shields.io/travis/codereverser/casparser)](https://travis-ci.org/github/codereverser/casparser)
[![Codecov](https://img.shields.io/codecov/c/github/codereverser/casparser)](https://codecov.io/gh/codereverser/casparser)
[![DeepSource](https://deepsource.io/gh/codereverser/casparser.svg/?label=active+issues)](https://deepsource.io/gh/codereverser/casparser/?ref=repository-badge)
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

```
import casparser
data = casparser.read_cas_pdf('/path/to/cas/pdf/file.pdf', 'password')
```

### Data structure

```json
{
    "statement_period": {
        "from": "YYYY-MMM-DD",
        "to": "YYYY-MMM-DD"
    },
    "file_type": "CAMS/KARVY/UNKNOWN",
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
                            "balance": "number"
                        }
                    ]
                }
            ]
        }
    ]
}
```


### CLI

```bash
Usage: casparser [-o output_file.json] [-p password] [-s] CAS_PDF_FILE

Options:
  -o, --output FILE  Output file path (json)
  -s, --summary      Print Summary of transactions parsed.
  -p PASSWORD        CAS password
  --version          Show the version and exit.
  --help             Show this message and exit.
``` 

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

PS:- select the detailed statement (including transactions) option