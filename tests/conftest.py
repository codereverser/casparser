"""Shared pytest fixtures for the e2e suite.

Each fixture skips its dependent tests when the corresponding env
var isn't set, so contributors without the encrypted sample bundle
can still run unit-level tests. The PDFs themselves are parsed once
per module via the `*_data` fixtures to keep wall-time low.

The encrypted fixtures (`tests/files.enc`) are decoded into
`tests/files/` by `.github/scripts/extract_files.sh` on CI; locally,
the same files live in `tests/files/` for tests that don't need a
password (`nsdl_statement_1.pdf`) and the rest are skipped.
"""

from __future__ import annotations

import os
import re

import pytest

# Make assertion-rewriting work for the shared invariant helpers, so
# failed assertions inside `_assertions.assert_*` show useful diffs
# instead of bare `AssertionError`.
pytest.register_assert_rewrite("tests._assertions")


# ANSI escape stripper used by CLI tests to make output assertions
# robust against `rich`-coloured TTY runs.
ANSI_RE = re.compile(r"\x1b\[([0-9,A-Z]{1,2}(;[0-9]{1,2})?(;[0-9]{3})?)?[m|K]?")


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def _required_env(var: str) -> str:
    val = os.getenv(var)
    if not val:
        pytest.skip(f"environment variable {var} not set")
    return val


# --- passwords --------------------------------------------------------------


@pytest.fixture(scope="session")
def cams_password() -> str:
    return _required_env("CAMS_CAS_PASSWORD")


@pytest.fixture(scope="session")
def kfin_password() -> str:
    return _required_env("KFINTECH_CAS_PASSWORD")


@pytest.fixture(scope="session")
def cdsl_password() -> str:
    # CDSL_CAS_PASSWORD may legitimately be empty for some samples.
    return os.getenv("CDSL_CAS_PASSWORD", "")


# --- file paths (skip on missing env var) -----------------------------------


@pytest.fixture(scope="session")
def cams_file() -> str:
    return _required_env("CAMS_CAS_FILE")


@pytest.fixture(scope="session")
def cams_file_new() -> str:
    return _required_env("CAMS_CAS_FILE_NEW")


@pytest.fixture(scope="session")
def cams_summary_file() -> str:
    return _required_env("CAMS_CAS_SUMMARY")


@pytest.fixture(scope="session")
def kfin_file() -> str:
    return _required_env("KFINTECH_CAS_FILE")


@pytest.fixture(scope="session")
def kfin_file_new() -> str:
    return _required_env("KFINTECH_CAS_FILE_NEW")


@pytest.fixture(scope="session")
def kfin_summary_file() -> str:
    return _required_env("KFINTECH_CAS_SUMMARY")


@pytest.fixture(scope="session")
def nsdl_file() -> str:
    return _required_env("NSDL_CAS_FILE_1")


@pytest.fixture(scope="session")
def cdsl_file() -> str:
    return _required_env("CDSL_CAS_FILE_1")


@pytest.fixture(scope="session")
def bad_file() -> str:
    return _required_env("BAD_CAS_FILE")


# --- parsed-data fixtures (one parse per module) ----------------------------
#
# Each `*_data` fixture parses its PDF exactly once per pytest module,
# so per-issuer test files share the work across their methods.


@pytest.fixture(scope="module")
def cams_data(cams_file, cams_password):
    from casparser import read_cas_pdf

    return read_cas_pdf(cams_file, cams_password)


@pytest.fixture(scope="module")
def cams_new_data(cams_file_new, cams_password):
    from casparser import read_cas_pdf

    return read_cas_pdf(cams_file_new, cams_password)


@pytest.fixture(scope="module")
def cams_summary_data(cams_summary_file, cams_password):
    from casparser import read_cas_pdf

    return read_cas_pdf(cams_summary_file, cams_password)


@pytest.fixture(scope="module")
def kfin_data(kfin_file, kfin_password):
    from casparser import read_cas_pdf

    return read_cas_pdf(kfin_file, kfin_password)


@pytest.fixture(scope="module")
def kfin_new_data(kfin_file_new, kfin_password):
    from casparser import read_cas_pdf

    return read_cas_pdf(kfin_file_new, kfin_password)


@pytest.fixture(scope="module")
def kfin_summary_data(kfin_summary_file, kfin_password):
    from casparser import read_cas_pdf

    return read_cas_pdf(kfin_summary_file, kfin_password)


@pytest.fixture(scope="module")
def nsdl_data(nsdl_file):
    from casparser import read_cas_pdf

    # NSDL_CAS_FILE_1 in the current bundle is not password-protected.
    return read_cas_pdf(nsdl_file, "")


@pytest.fixture(scope="module")
def cdsl_data(cdsl_file, cdsl_password):
    from casparser import read_cas_pdf

    return read_cas_pdf(cdsl_file, cdsl_password)
