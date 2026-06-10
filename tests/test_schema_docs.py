"""Guards against the docs drifting from the code.

Two contracts are checked:

- the generated JSON Schema files under ``schema/`` match what the
  current pydantic models produce (regenerate with
  ``uv run python scripts/generate_schema.py``);
- the human-facing shape documentation in ``README.md`` mentions every
  enum member and model field it claims to describe.
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest

from casparser.enums import TransactionType
from casparser.types import (
    Bond,
    CASData,
    DematAccount,
    DematOwner,
    Equity,
    Folio,
    InvestorInfo,
    MutualFund,
    NSDLCASData,
    Scheme,
    SchemeValuation,
    StatementPeriod,
    TransactionData,
)

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text()


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_schema", ROOT / "scripts" / "generate_schema.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_schema_files_match_models():
    """schema/*.schema.json must be regenerated whenever the models change."""
    gen = _load_generator()
    for stem, schema in gen.build_schemas().items():
        path = ROOT / "schema" / f"{stem}.schema.json"
        assert path.exists(), f"{path.name} missing — run scripts/generate_schema.py"
        on_disk = json.loads(path.read_text())
        assert on_disk == schema, f"schema/{path.name} is stale — run scripts/generate_schema.py"


def test_readme_lists_every_transaction_type():
    """The transaction-type table must cover the full enum, verbatim.

    Word-boundary match so e.g. a stale ``DIVIDEND_REINVESTMENT`` in the
    README does not satisfy ``DIVIDEND_REINVEST``.
    """
    missing = [t.name for t in TransactionType if not re.search(rf"\b{t.name}\b", README)]
    assert not missing, f"README transaction-type table is missing: {missing}"


@pytest.mark.parametrize(
    "model",
    [
        StatementPeriod,
        InvestorInfo,
        TransactionData,
        SchemeValuation,
        Scheme,
        Folio,
        CASData,
        DematOwner,
        Equity,
        Bond,
        MutualFund,
        DematAccount,
        NSDLCASData,
    ],
)
def test_readme_documents_every_field(model):
    """Every serialised field name must appear in the README shape sketches."""
    missing = []
    for name, field in model.model_fields.items():
        key = field.alias or name
        if not re.search(rf"\b{re.escape(key)}\b", README):
            missing.append(key)
    assert not missing, f"README is missing {model.__name__} fields: {missing}"
