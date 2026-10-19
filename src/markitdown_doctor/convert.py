"""
Conversion with a verdict.

Wraps MarkItDown's public API without modifying it. Two behaviours are added,
both of which address failures observed in real use:

1. **Empty results are reported, not swallowed.** MarkItDown returns exit code 0
   and a single newline for an image with no OCR backend, and for a scanned PDF
   with no text layer. Downstream that becomes an empty Markdown file that looks
   like a successful conversion. Here it becomes an ``EMPTY_OUTPUT`` verdict.

2. **Exceptions become sentences.** MarkItDown's exceptions are accurate but
   addressed to Python developers (``FileConversionException`` wrapping a
   ``MissingDependencyException`` inside 30 lines of traceback). Here they are
   mapped to a cause plus a runnable fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .formats import install_command, lookup
from .preflight import Check, Status


class Verdict(str, Enum):
    """How a conversion attempt ended."""

    OK = "ok"
    EMPTY = "empty"            # ran fine, produced nothing useful
    GARBLED = "garbled"        # ran fine, produced mojibake from raw bytes
    SKIPPED = "skipped"        # preflight said don't bother
    FAILED = "failed"          # converter raised
    TIMEOUT = "timeout"


@dataclass
class Result:
    """Outcome of converting one file."""

    path: Path
    verdict: Verdict
    markdown: str = ""
    chars: int = 0
    elapsed_ms: int = 0
    cause: str = ""
    fix: str = ""
    # Why a SKIPPED result was skipped. A missing pip extra and an unsupported
    # format both skip, but they need different wording and different fixes.
    status: Status | None = None

    @property
    def ok(self) -> bool:
        return self.verdict is Verdict.OK

    @property
    def useful(self) -> bool:
        """True only when there is text a downstream consumer can use."""
        return self.verdict is Verdict.OK and self.chars > 0


# Characters that count as "content" -- whitespace-only output is still empty.
_MEANINGFUL = str.maketrans("", "")


def _meaningful_chars(text: str) -> int:
    stripped = text.strip().translate(_MEANINGFUL)
    return len(stripped)


def _explain_exception(exc: BaseException, extension: str) -> tuple[str, str]:
    """Translate a MarkItDown exception into (cause, fix).

    Walks the exception chain because MarkItDown wraps the useful
    ``MissingDependencyException`` inside a ``FileConversionException``.
    """
    seen: list[str] = []
    node: BaseException | None = exc
    depth = 0
    while node is not None and depth < 6:
        seen.append(f"{type(node).__name__}: {node}")
        node = node.__cause__ or node.__context__
        depth += 1

    blob = " | ".join(seen)
    lowered = blob.lower()

    if "missingdependency" in lowered or "dependencies needed to read" in lowered:
        spec = lookup(extension)
        extra = spec.extra if spec and spec.extra else None
        cause = (
            "The converter for {} is built in, but the libraries it needs are not "
            "installed. A bare `pip install markitdown` does not include them.".format(
                extension or "this format"
            )
        )
        fix = install_command([extra]) if extra else "pip install 'markitdown[all]'"
        return cause, fix

    if "permission denied" in lowered and "errno 13" in lowered:
        return (
            "The path is a directory (or unreadable). MarkItDown only converts "
            "files; it reports a folder as a permission error.",
            "Use `mdc convert <dir> -r` for directories, or pass a single file.",
        )

    if "no such file" in lowered or "filenotfound" in lowered:
        return ("The file does not exist at that path.", "Re-check the path.")

    if "unsupportedformat" in lowered or "no converter attempted" in lowered:
        return (
            "No built-in converter claimed this content, even after sniffing it "
            "with magika.",
            "Check `mdc formats` for supported types, or pass `-x <ext>` if the "
            "filename hides the real format.",
        )

    if "encrypted" in lowered or "password" in lowered:
        return (
            "The document is password protected.",
            "Remove the password in the source application, then retry.",
        )

    first = seen[0] if seen else repr(exc)
    return (first, "Re-run with `--traceback` to see the full stack.")


def _looks_binary(text: str) -> bool:
    """Heuristic: did we just read raw bytes as if they were text?

    MarkItDown's PlainTextConverter is a catch-all with the lowest priority. When
    no real converter claims a file, it decodes the bytes with a guessed charset,
    so a corrupt archive or a proprietary format comes back as mojibake and is
    reported as a successful conversion. Downstream that is worse than an error,
    because it looks like content.

    The test is deliberately conservative: control characters other than tab,
    newline and carriage return are a strong signal of binary data, and real
    documents essentially never contain them.
    """
    if not text:
        return False
    sample = text[:4000]
    control = sum(
        1 for ch in sample if ord(ch) < 32 and ch not in "\t\r\n\f\v"
    )
    replacement = sample.count("\ufffd")
    return (control + replacement) / len(sample) > 0.02


def convert_one(
    path: Path,
    *,
    md=None,
    check: Check | None = None,
    extension_hint: str | None = None,
    timeout: float | None = None,
) -> Result:
    """Convert one file and return a verdict instead of raising.

    Args:
        md: an existing ``MarkItDown`` instance, so batch runs reuse one
            instance (magika model load is the expensive part).
        check: a preflight result; a blocking one short-circuits conversion.
        extension_hint: overrides the extension MarkItDown infers from the
            filename. Needed when a file is misnamed (``report.docx`` that is
            actually a PDF), which otherwise silently takes the wrong converter.
    """
    import time

    if check is not None and check.blocking:
        return Result(
            path=path,
            verdict=Verdict.SKIPPED,
            cause=check.reason,
            fix=check.fix,
            status=check.status,
        )

    if md is None:
        from markitdown import MarkItDown

        md = MarkItDown()

    extension = path.suffix.lower()
    stream_info = None
    if extension_hint:
        from markitdown import StreamInfo

        hint = extension_hint.strip().lower()
        if not hint.startswith("."):
            hint = "." + hint
        stream_info = StreamInfo(extension=hint)
        extension = hint

    started = time.perf_counter()

    try:
        result = md.convert(str(path), stream_info=stream_info)
    except Exception as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        cause, fix = _explain_exception(exc, extension)
        return Result(
            path=path,
            verdict=Verdict.FAILED,
            elapsed_ms=elapsed,
            cause=cause,
            fix=fix,
        )

    elapsed = int((time.perf_counter() - started) * 1000)
    text = result.markdown or ""
    chars = _meaningful_chars(text)

    if chars == 0:
        return Result(
            path=path,
            verdict=Verdict.EMPTY,
            markdown=text,
            chars=0,
            elapsed_ms=elapsed,
            cause=_empty_cause(extension),
            fix=_empty_fix(extension),
        )

    if _looks_binary(text):
        return Result(
            path=path,
            verdict=Verdict.GARBLED,
            markdown=text,
            chars=chars,
            elapsed_ms=elapsed,
            cause=(
                "No real converter claimed this file, so MarkItDown fell back to "
                "reading the raw bytes as text. The result is mojibake, not content, "
                "even though the conversion reported success."
            ),
            fix=(
                "Confirm the file is not corrupt and that its format is supported "
                "(`mdc formats`). If the extension is wrong, pass `-x <real-ext>`."
            ),
        )

    return Result(
        path=path,
        verdict=Verdict.OK,
        markdown=text,
        chars=chars,
        elapsed_ms=elapsed,
    )


def _empty_cause(extension: str) -> str:
    if extension in {".pdf"}:
        return (
            "The PDF parsed cleanly but yielded no text. This is typical of a "
            "scanned document (images only, no text layer). MarkItDown does not "
            "OCR by default, so it returns an empty string with exit code 0."
        )
    if extension in {".jpg", ".jpeg", ".png"}:
        return (
            "Images are only described via exiftool metadata and an optional "
            "OCR/LLM backend. With neither configured, the conversion succeeds "
            "and produces nothing."
        )
    return (
        "The converter completed without error but produced no text content. "
        "The source may be empty, or its content may be non-textual."
    )


def _empty_fix(extension: str) -> str:
    if extension in {".pdf"}:
        return (
            "Run OCR first (`ocrmypdf in.pdf out.pdf`) or install the OCR plugin: "
            "`pip install markitdown-ocr`."
        )
    if extension in {".jpg", ".jpeg", ".png"}:
        return (
            "Install exiftool for metadata, or the OCR plugin for text: "
            "`pip install markitdown-ocr`."
        )
    return "Inspect the source file; there may be nothing to extract."
