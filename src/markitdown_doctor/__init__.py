"""markitdown-doctor - preflight diagnosis and readable failures for MarkItDown."""

from .__about__ import __version__
from .batch import BatchReport, build_plan, run_batch
from .convert import Result, Verdict, convert_one
from .formats import (
    BUILTIN_FORMATS,
    EXTRA_FORMATS,
    FormatSpec,
    install_command,
    lookup,
)
from .preflight import (
    Check,
    EnvironmentReport,
    Status,
    check_path,
    diagnose_environment,
)

__all__ = [
    "BUILTIN_FORMATS",
    "EXTRA_FORMATS",
    "BatchReport",
    "Check",
    "EnvironmentReport",
    "FormatSpec",
    "Result",
    "Status",
    "Verdict",
    "__version__",
    "build_plan",
    "check_path",
    "convert_one",
    "diagnose_environment",
    "install_command",
    "lookup",
    "run_batch",
]
