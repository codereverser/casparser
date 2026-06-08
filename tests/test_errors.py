"""Error-path and back-compat tests.

Covers:
  * Incorrect password → `IncorrectPasswordError`
  * Non-PDF / corrupt input → `CASParseError`
  * Unrecognised CAS issuer → `CASParseError`
  * Deprecated `force_pdfminer=True` kwarg still parses (DeprecationWarning)
"""

from __future__ import annotations

import io
import warnings

import pytest

from casparser import read_cas_pdf
from casparser.exceptions import CASParseError, IncorrectPasswordError


class TestPasswordErrors:
    def test_incorrect_password_raises(self, cams_file):
        with pytest.raises(IncorrectPasswordError) as exc:
            read_cas_pdf(cams_file, "")
        assert "Incorrect PDF password!" in str(exc.value)


class TestInputValidation:
    def test_non_pdf_buffer_raises_cas_parse_error(self):
        with io.BytesIO(b"this is not a pdf") as fp, pytest.raises(CASParseError) as exc:
            read_cas_pdf(fp, "")
        msg = str(exc.value)
        assert "Unhandled error while opening" in msg or "Could not" in msg

    def test_non_pdf_typeerror_wraps_as_cas_parse_error(self):
        """Passing a wrong-type input (here: an int) surfaces as
        `CASParseError`, not a raw `TypeError`."""
        with pytest.raises(CASParseError):
            read_cas_pdf(1, "")

    def test_unknown_issuer_pdf_raises(self, tmp_path):
        """A valid PDF without any CAS marker reports the issuer-
        detection failure cleanly."""
        import pypdfium2 as pdfium

        pdf_path = tmp_path / "blank.pdf"
        pdf = pdfium.PdfDocument.new()
        pdf.new_page(595, 842)
        pdf.save(str(pdf_path))
        with pytest.raises(CASParseError) as exc:
            read_cas_pdf(str(pdf_path), "")
        assert "Could not identify" in str(exc.value)


class TestBackCompatShims:
    def test_force_pdfminer_deprecated(self, cams_file, cams_password):
        """`force_pdfminer=True` is preserved as a no-op + emits a
        `DeprecationWarning`. Removing it would break callers that
        carried the kwarg over from <1.0 releases."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            data = read_cas_pdf(
                cams_file,
                cams_password,
                force_pdfminer=True,
            )
        assert any(
            issubclass(w.category, DeprecationWarning) and "force_pdfminer" in str(w.message)
            for w in caught
        ), "expected DeprecationWarning mentioning force_pdfminer"
        # Result is identical to a normal parse (back-compat).
        assert data.folios
