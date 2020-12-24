# Changelog

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

- **Breaking Change**: `folios` is a list instead of dict, so that the order is 
 preserved during format conversion to other data types like json.  
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

- removed support for python < 3.8 versions
- Better investor info parser

## 0.1.2 - 2020-10-14

- Support for parsing investor info 

## 0.1.1 - 2020-10-14

- Support for parsing folios without PAN

## 0.1.0 - 2020-10-11

- Initial release