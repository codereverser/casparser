# CASParser
[![code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![GitHub](https://img.shields.io/github/license/codereverser/casparser)](https://github.com/codereverser/casparser/blob/main/LICENSE)
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
[License](#license-info) section for more details 
 

## Usage

```
import casparser
data = casparser.read_cas_pdf('/path/to/cas/pdf/file.pdf', 'password')
```

#### CLI

```bash
Usage: casparser [-o output_file.json] [-p password] [-s] CAS_PDF_FILE

Options:
  -o, --output FILE  Output file path (json)
  -s, --summary      Print Summary of transactions parsed.
  -p PASSWORD        CAS password
  --version          Show the version and exit.
  --help             Show this message and exit.
``` 

##### Demo

![demo](https://raw.githubusercontent.com/codereverser/casparser/main/assets/demo.jpg)


## License
<a name="license-info"></a>

CASParser is distributed under MIT license by default. However enabling the optional dependency
`mupdf` would imply the use of [PyMuPDF](https://github.com/pymupdf/PyMuPDF) /
[MuPDF](https://mupdf.com/license.html) and hence the licenses GNU GPL v3 and GNU Affero GPL v3 
would apply. Copies of all licenses have been included in this repository. - _IANAL_
 