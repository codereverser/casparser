from typing import TYPE_CHECKING

from .parsers import read_cas_pdf
from .types import CASData

if TYPE_CHECKING:
    # Re-exported lazily at runtime via __getattr__ (see below); declared here
    # so type checkers and IDEs still resolve `casparser.CapitalGainsReport`.
    from .analysis import CapitalGainsReport

__all__ = [
    "read_cas_pdf",
    "__version__",
    "CASData",
    "CapitalGainsReport",
]

__version__ = "1.4.1"


def __getattr__(name: str):
    # The capital-gains machinery (`casparser.analysis`) pulls in a large
    # subtree that the parsing path never touches. Defer its import until
    # `CapitalGainsReport` is first accessed so plain `import casparser` +
    # `read_cas_pdf(...)` stays lightweight. (PEP 562.)
    if name == "CapitalGainsReport":
        from .analysis import CapitalGainsReport

        return CapitalGainsReport
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
