#!/usr/bin/env python
"""Generate JSON Schema files for casparser's public output models.

The README documents the output shape for humans; the files under
``schema/`` are the machine-readable contract, generated from the
pydantic models in ``casparser.types``. Regenerate after any model
change:

    uv run python scripts/generate_schema.py

``tests/test_schema_docs.py`` fails when the checked-in files drift
from the models, so a forgotten regeneration is caught in CI.
"""

from __future__ import annotations

import json
from pathlib import Path

from casparser.types import CASData, NSDLCASData

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"

# JSON Schema dialect emitted by pydantic v2.
DIALECT = "https://json-schema.org/draft/2020-12/schema"

# One schema file per public top-level output model, named after the
# model so the mapping stays self-evident.
MODELS = {
    "CASData": CASData,  # CAMS / KFintech (RTA) statements
    "NSDLCASData": NSDLCASData,  # NSDL / CDSL demat statements
}


def build_schemas() -> dict[str, dict]:
    """Return ``{file-stem: schema-dict}`` for every public output model.

    ``mode="serialization"`` + ``by_alias=True`` describe the JSON the
    library *emits* (``read_cas_pdf(..., output="json")``), not what the
    models would accept on input — e.g. ``Decimal`` fields appear as
    precision-preserving JSON strings.
    """
    out: dict[str, dict] = {}
    for stem, model in MODELS.items():
        schema = model.model_json_schema(by_alias=True, mode="serialization")
        out[stem] = {"$schema": DIALECT, **schema}
    return out


def main() -> None:
    SCHEMA_DIR.mkdir(exist_ok=True)
    for stem, schema in build_schemas().items():
        path = SCHEMA_DIR / f"{stem}.schema.json"
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
        print(f"wrote {path.relative_to(SCHEMA_DIR.parent)}")


if __name__ == "__main__":
    main()
