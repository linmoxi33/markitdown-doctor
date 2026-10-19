"""
Preflight diagnosis: answer "will this file convert, and if not, what do I run?"

The whole point is to fail *before* handing the file to MarkItDown, so the user
never sees a Python traceback for a problem with a one-line fix.
"""

from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .formats import (
    EMPTY_RISK_FORMATS,
    EXTERNAL_TOOLS,
    IMAGE_EXTENSIONS,
    UNSUPPORTED_FIXES,
    UNSUPPORTED_FORMATS,
    FormatSpec,
    install_command,
    lookup,
)


class Status(str, Enum):
    """Outcome of a preflight check for one file."""

    READY = "ready"              # will convert, converter present
    NEEDS_EXTRA = "needs_extra"  # known format, pip extra missing
    UNSUPPORTED = "unsupported"  # MarkItDown has no converter at all
    UNKNOWN = "unknown"          # extension not in our matrix
    MISSING = "missing"          # path does not exist
    IS_DIRECTORY = "is_directory"
    EMPTY_FILE = "empty"         # zero bytes on disk
    LIKELY_EMPTY = "likely_empty"  # converts "fine" but yields no text


# Statuses that mean "converting this will not produce useful output".
BLOCKING = frozenset(
    {
        Status.NEEDS_EXTRA,
        Status.UNSUPPORTED,
        Status.MISSING,
        Status.IS_DIRECTORY,
        Status.EMPTY_FILE,
        Status.LIKELY_EMPTY,
    }
)


@dataclass
class Check:
    """Result of preflighting a single path."""

    path: Path
    status: Status
    extension: str
    spec: FormatSpec | None = None
    reason: str = ""
    fix: str = ""
    missing_modules: tuple[str, ...] = ()
    missing_tools: tuple[str, ...] = ()
    size: int = 0

    @property
    def ok(self) -> bool:
        return self.status is Status.READY

    @property
    def blocking(self) -> bool:
        return self.status in BLOCKING


def _missing_modules(spec: FormatSpec) -> tuple[str, ...]:
    """Which of a converter's import names are absent from this interpreter."""
    absent = []
    for module in spec.modules:
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            absent.append(module)
    return tuple(absent)


def _missing_tools(extension: str) -> tuple[str, ...]:
    """External binaries this extension needs but that are not on PATH."""
    absent = []
    for tool in EXTERNAL_TOOLS:
        if extension in tool.required_for and shutil.which(tool.probe) is None:
            absent.append(tool.name)
    return tuple(absent)


def check_path(path: str | Path, *, check_tools: bool = True) -> Check:
    """Preflight a single path without touching MarkItDown.

    Cheap by design: only metadata lookups and ``find_spec`` probes, so it can
    run across thousands of files before a batch job starts.
    """
    p = Path(path)
    extension = p.suffix.lower()

    if not p.exists():
        return Check(
            path=p,
            status=Status.MISSING,
            extension=extension,
            reason="Path does not exist.",
            fix="Check the spelling, or quote the path if it contains spaces.",
        )

    if p.is_dir():
        return Check(
            path=p,
            status=Status.IS_DIRECTORY,
            extension=extension,
            reason="MarkItDown converts files, not directories. Passing a folder "
            "raises 'PermissionError: [Errno 13]' which reads like a permissions bug.",
            fix="Use `mdc convert <dir> -r` to convert the whole tree, or pass one file.",
        )

    try:
        size = p.stat().st_size
    except OSError:
        size = 0

    if size == 0:
        return Check(
            path=p,
            status=Status.EMPTY_FILE,
            extension=extension,
            size=0,
            reason="File is zero bytes; nothing to convert.",
            fix="Re-download or re-export the source file.",
        )

    # Images convert "successfully" into nothing unless a metadata or OCR
    # backend exists. Surfacing this before conversion is the whole point.
    if extension in IMAGE_EXTENSIONS:
        has_exiftool = shutil.which("exiftool") is not None
        if has_exiftool:
            return Check(
                path=p,
                status=Status.READY,
                extension=extension,
                size=size,
                reason="exiftool is present, so EXIF metadata will be extracted. "
                "Text inside the image still needs an OCR backend.",
            )
        return Check(
            path=p,
            status=Status.LIKELY_EMPTY,
            extension=extension,
            size=size,
            reason=EMPTY_RISK_FORMATS[extension],
            fix="Install exiftool for metadata (winget install OliverBetz.ExifTool), "
            "or `pip install markitdown-ocr` for text. Otherwise skip images.",
        )

    # Known-but-unsupported takes precedence: these deserve the specific hint.
    if extension in UNSUPPORTED_FORMATS:
        return Check(
            path=p,
            status=Status.UNSUPPORTED,
            extension=extension,
            size=size,
            reason=UNSUPPORTED_FORMATS[extension],
            fix=UNSUPPORTED_FIXES.get(
                extension,
                "Convert it to a supported format first; run `mdc formats` for the table.",
            ),
        )

    spec = lookup(extension)
    if spec is None:
        return Check(
            path=p,
            status=Status.UNKNOWN,
            extension=extension or "(no extension)",
            size=size,
            reason="Extension is not in the capability matrix, so the outcome is "
            "uncertain. MarkItDown sniffs content with magika, so it may still work.",
            fix="Run `mdc convert` on it and read the verdict; use `-x` to pass an "
            "extension hint when the filename is misleading.",
        )

    if spec.builtin:
        return Check(
            path=p,
            status=Status.READY,
            extension=extension,
            spec=spec,
            size=size,
            reason=spec.notes or "Built-in converter, no extra needed.",
        )

    absent = _missing_modules(spec)
    if absent:
        return Check(
            path=p,
            status=Status.NEEDS_EXTRA,
            extension=extension,
            spec=spec,
            size=size,
            missing_modules=absent,
            reason="{} converter is registered but its dependency is not installed "
            "(missing: {}).".format(spec.label, ", ".join(absent)),
            fix=install_command([spec.extra]) or "",
        )

    tools = _missing_tools(extension) if check_tools else ()
    if tools:
        return Check(
            path=p,
            status=Status.NEEDS_EXTRA,
            extension=extension,
            spec=spec,
            size=size,
            missing_tools=tools,
            reason="{} converter is installed but needs the {} binary on PATH.".format(
                spec.label, ", ".join(tools)
            ),
            fix="; ".join(
                t.install_hint for t in EXTERNAL_TOOLS if t.name in tools
            ),
        )

    return Check(
        path=p,
        status=Status.READY,
        extension=extension,
        spec=spec,
        size=size,
        reason="Converter and dependencies present.",
    )


@dataclass
class EnvironmentReport:
    """What this Python environment can and cannot convert."""

    python_version: str
    markitdown_version: str
    ready_extras: list[str] = field(default_factory=list)
    missing_extras: dict[str, list[str]] = field(default_factory=dict)
    missing_tools: list[str] = field(default_factory=list)

    @property
    def install_hint(self) -> str | None:
        if not self.missing_extras:
            return None
        return install_command(self.missing_extras.keys())


def diagnose_environment() -> EnvironmentReport:
    """Report which MarkItDown extras this interpreter actually has."""
    import sys

    try:
        from importlib.metadata import version

        md_version = version("markitdown")
    except Exception:  # pragma: no cover - markitdown is a hard dependency
        md_version = "not installed"

    report = EnvironmentReport(
        python_version=".".join(str(v) for v in sys.version_info[:3]),
        markitdown_version=md_version,
    )

    from .formats import EXTRA_FORMATS

    for spec in EXTRA_FORMATS:
        if not spec.extra:
            continue
        if _missing_modules(spec):
            report.missing_extras.setdefault(spec.extra, []).append(spec.extension)
        elif spec.extra not in report.ready_extras:
            report.ready_extras.append(spec.extra)

    for tool in EXTERNAL_TOOLS:
        if shutil.which(tool.probe) is None:
            report.missing_tools.append(tool.name)

    return report


def collect_inputs(target: str | Path, *, recursive: bool = False) -> list[Path]:
    """Expand a file or directory argument into a deterministic file list."""
    p = Path(target)
    if p.is_file():
        return [p]
    if p.is_dir():
        pattern = "**/*" if recursive else "*"
        files = [f for f in p.glob(pattern) if f.is_file()]
        return sorted(files)
    return [p]
