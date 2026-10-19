"""
Command line interface.

Design rules, all of them driven by observed failures:

* Every message names a cause AND a runnable next command.
* Exit codes are meaningful so shell pipelines can react:
      0  everything converted with content
      1  partial: some files failed, were empty, or were skipped
      2  nothing converted / usage problem
* No traceback unless the user asks for one with ``--traceback``.
* Colour is opt-out (``NO_COLOR``) and never the only carrier of meaning.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .__about__ import __version__
from .batch import run_batch
from .convert import Verdict, convert_one
from .formats import (
    BUILTIN_FORMATS,
    EMPTY_RISK_FORMATS,
    EXTERNAL_TOOLS,
    EXTRA_FORMATS,
    UNSUPPORTED_FORMATS,
    extensions_by_extra,
    install_command,
)
from .preflight import Status, check_path, collect_inputs, diagnose_environment

# --- output helpers ------------------------------------------------------


def _supports_colour() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


_COLOUR = _supports_colour()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOUR else text


def ok(text: str) -> str:
    return _c(text, "32")


def warn(text: str) -> str:
    return _c(text, "33")


def bad(text: str) -> str:
    return _c(text, "31")


def dim(text: str) -> str:
    return _c(text, "2")


def bold(text: str) -> str:
    return _c(text, "1")


def emit(line: str = "") -> None:
    """Write one line, tolerating consoles that cannot encode the characters."""
    try:
        print(line)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(line.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def _write_stdout(text: str) -> None:
    """Print Markdown exactly as `markitdown` does, byte for byte.

    MarkItDown's own CLI does ``print(markdown)``, which appends a newline and
    translates ``\\n`` to the platform separator. Matching that exactly means
    ``mdc convert x.pdf > x.md`` produces the same bytes as the original tool,
    so this wrapper is a drop-in replacement in existing pipelines.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.write(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))
        sys.stdout.write("\n")


def human_size(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} GB"


STATUS_STYLE = {
    Status.READY: ("OK", ok),
    Status.NEEDS_EXTRA: ("MISSING DEP", warn),
    Status.UNSUPPORTED: ("UNSUPPORTED", bad),
    Status.UNKNOWN: ("UNKNOWN", warn),
    Status.MISSING: ("NO FILE", bad),
    Status.IS_DIRECTORY: ("IS DIR", warn),
    Status.EMPTY_FILE: ("EMPTY", warn),
    Status.LIKELY_EMPTY: ("NO TEXT", warn),
}


# --- commands ------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    report = diagnose_environment()

    emit(bold("MarkItDown environment"))
    emit(f"  python       {report.python_version}")
    emit(f"  markitdown   {report.markitdown_version}")
    emit("")

    # Without markitdown itself there is nothing to diagnose; say so plainly
    # instead of listing seven "not installed" extras.
    if report.markitdown_version == "not installed":
        emit(bad("MarkItDown is not installed in this environment."))
        emit("")
        emit(bold("Install it, then run this again:"))
        emit(f"  {ok('pip install markitdown-doctor')}")
        emit(dim("  (or: pip install 'markitdown-doctor[all]' for every format)"))
        return 2

    if report.ready_extras:
        emit(bold("Ready"))
        for extra in report.ready_extras:
            exts = ", ".join(extensions_by_extra().get(extra, []))
            emit(f"  {ok('[x]')} {extra:<22} {dim(exts)}")
        emit("")

    if report.missing_extras:
        emit(bold("Not installed"))
        for extra, exts in report.missing_extras.items():
            emit(f"  {warn('[ ]')} {extra:<22} {dim(', '.join(exts))}")
        emit("")

    if report.missing_tools:
        emit(bold("External tools not on PATH"))
        for name in report.missing_tools:
            tool = next(t for t in EXTERNAL_TOOLS if t.name == name)
            emit(f"  {warn('[ ]')} {name:<22} {dim(tool.reason)}")
            emit(f"      {dim(tool.install_hint)}")
        emit("")

    hint = report.install_hint
    if hint:
        emit(bold("To unlock the missing formats:"))
        emit(f"  {ok(hint)}")
        emit(dim("  Or install everything at once: pip install 'markitdown[all]'"))
        return 1

    if report.missing_tools:
        emit(
            ok("All Python converters are available.")
            + dim(" Optional tools above only enrich output; conversion still works.")
        )
        return 0

    emit(ok("All formats in the matrix are available."))
    return 0


def cmd_formats(args: argparse.Namespace) -> int:
    emit(bold("Formats that work on a bare `pip install markitdown`"))
    for spec in BUILTIN_FORMATS:
        emit(f"  {ok('[x]')} {spec.extension:<8} {spec.label}")
    emit("")

    # Show the real install state, not a static list: a format table that says
    # "needs an extra" for something already installed is actively misleading.
    report = diagnose_environment()
    installed = set(report.ready_extras)

    emit(bold("Formats that need an extra"))
    for spec in EXTRA_FORMATS:
        if spec.extra in installed:
            emit(
                f"  {ok('[x]')} {spec.extension:<8} {spec.label:<20} {dim(spec.display_extra)}"
            )
        else:
            emit(
                f"  {warn('[ ]')} {spec.extension:<8} {spec.label:<20} {dim(spec.display_extra)}"
            )
        if spec.notes:
            emit(f"          {dim(spec.notes)}")
    emit("")
    emit(dim("  [x] already installed    [ ] needs `pip install '...'`"))
    emit("")

    emit(bold("Commonly expected, but NOT supported"))
    for ext, note in UNSUPPORTED_FORMATS.items():
        emit(f"  {bad('[!]')} {ext:<8} {note}")
    emit("")

    emit(bold("Accepted, but produces NO text without extra setup"))
    for ext, note in EMPTY_RISK_FORMATS.items():
        emit(f"  {warn('[~]')} {ext:<8} {note}")
    emit("")

    emit(dim("Install one format:      pip install 'markitdown[pdf]'"))
    emit(dim("Install several:         pip install 'markitdown[pdf,docx,pptx]'"))
    emit(dim("Install everything:      pip install 'markitdown[all]'"))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    paths: list[Path] = []
    for target in args.targets:
        paths.extend(collect_inputs(target, recursive=args.recursive))

    if not paths:
        emit(bad("No input files found."))
        return 2

    # Check each path once and keep the results: re-running check_path for the
    # install hint would double the work on large inputs.
    checks = [check_path(p) for p in paths]

    blocked = 0
    extras: list[str] = []
    for check in checks:
        tag, style = STATUS_STYLE.get(check.status, ("?", dim))
        emit(f"{style(tag.ljust(11))} {check.path}")
        if check.blocking:
            blocked += 1
            emit(f"            {dim('why:')} {check.reason}")
            if check.fix:
                emit(f"            {dim('fix:')} {ok(check.fix)}")
        if (
            check.status is Status.NEEDS_EXTRA
            and check.spec
            and check.spec.extra
            and check.spec.extra not in extras
        ):
            extras.append(check.spec.extra)

    emit("")
    emit(f"{len(paths)} file(s) checked, {len(paths) - blocked} ready, {blocked} blocked.")

    command = install_command(extras) if (blocked and args.install_hint) else None
    if command:
        emit("")
        emit(bold("Unblock them with:"))
        emit(f"  {ok(command)}")

    return 0 if blocked == 0 else 1


def cmd_convert(args: argparse.Namespace) -> int:
    target = Path(args.target)
    if not target.exists():
        emit(bad(f"No such file or directory: {target}"))
        return 2

    is_dir = target.is_dir()

    # Single file, stdout mode: behave like the original CLI so scripts keep working.
    if not is_dir and args.output is None and not args.json:
        check = check_path(target)
        result = convert_one(target, check=check, extension_hint=args.extension)
        if result.ok:
            # Byte-for-byte identical to `markitdown file.pdf`, including the
            # trailing newline that print() adds, so existing pipelines and
            # diff-based tests keep working unchanged.
            _write_stdout(result.markdown)
            return 0
        _print_failure(target, result)
        return 2 if result.verdict is Verdict.FAILED else 1

    report = run_batch(
        target,
        recursive=args.recursive,
        output_dir=args.output,
        dry_run=args.dry_run,
        keep_empty=args.keep_empty,
        on_progress=None,  # one report at the end beats two interleaved streams
    )

    if args.json:
        emit(json.dumps(_report_as_dict(report), indent=2, ensure_ascii=False))
    elif not args.quiet:
        _print_report(report, dry_run=args.dry_run)

    summary = report.summary()
    if summary["failed"] or summary["empty"] or summary["garbled"]:
        return 1
    if summary["ok"] == 0 and not args.dry_run:
        return 2
    if args.dry_run and summary["planned"] == 0:
        return 2
    return 0


def _progress(item, result) -> None:
    if result is None:
        emit(f"  {dim('skip')} {item.relative}  {dim(item.check.reason.splitlines()[0])}")
        return
    if result.verdict is Verdict.OK:
        detail = f"{result.chars} chars, {result.elapsed_ms} ms"
        emit(f"  {ok('ok  ')} {item.relative}  {dim(detail)}")
    elif result.verdict is Verdict.EMPTY:
        emit(f"  {warn('empty')} {item.relative}  {dim('no text extracted')}")
    else:
        emit(f"  {bad('fail')} {item.relative}  {dim(result.cause.splitlines()[0])}")


def _print_failure(path: Path, result) -> None:
    # A skipped result can mean several things. Naming the actual status keeps
    # "you need a pip install" distinct from "this format is not supported".
    if result.verdict is Verdict.SKIPPED:
        if result.status is Status.NEEDS_EXTRA:
            emit(warn(f"Missing dependency: {path}"))
        elif result.status is Status.LIKELY_EMPTY:
            emit(warn(f"Would produce no text: {path}"))
        elif result.status is Status.UNSUPPORTED:
            emit(warn(f"Unsupported format: {path}"))
        else:
            emit(warn(f"Skipped: {path}"))
    elif result.verdict is Verdict.EMPTY:
        emit(warn(f"No text extracted: {path}"))
    elif result.verdict is Verdict.GARBLED:
        emit(warn(f"Not real content: {path}"))
    else:
        emit(bad(f"Failed: {path}"))
    emit(f"  {dim('cause:')} {result.cause}")
    if result.fix:
        emit(f"  {dim('fix:  ')} {ok(result.fix)}")


def _print_report(report, *, dry_run: bool) -> None:
    summary = report.summary()

    if dry_run:
        emit(bold("Dry run - nothing written"))
        emit("")

    for item, result in report.all_entries:
        if result.verdict is Verdict.OK:
            detail = f"{result.chars} chars"
            emit(f"  {ok('ok   ')} {item.relative} -> {item.destination}  {dim(detail)}")
        elif result.verdict is Verdict.EMPTY:
            emit(f"  {warn('empty')} {item.relative}  {dim('no text extracted, not written')}")
            emit(f"        {dim(result.cause.splitlines()[0])}")
            if result.fix:
                emit(f"        {dim('fix:')} {ok(result.fix)}")
        elif result.verdict is Verdict.GARBLED:
            emit(f"  {bad('junk ')} {item.relative}  {dim('binary read as text, not written')}")
            emit(f"        {dim(result.cause.splitlines()[0])}")
            if result.fix:
                emit(f"        {dim('fix:')} {ok(result.fix)}")
        else:
            emit(f"  {bad('fail ')} {item.relative}")
            emit(f"        {dim(result.cause.splitlines()[0])}")
            if result.fix:
                emit(f"        {dim('fix:')} {ok(result.fix)}")

    for item in report.planned:
        emit(f"  {ok('will ')} {item.relative} -> {item.destination}")

    for item in report.skipped:
        emit(f"  {dim('skip ')} {item.relative}  {dim(item.check.reason.splitlines()[0])}")
        if item.check.fix:
            emit(f"        {dim('fix:')} {ok(item.check.fix)}")

    emit("")
    if dry_run:
        emit(
            bold("Would convert: ")
            + f"{summary['planned']} file(s), "
            + f"{summary['skipped']} would be skipped "
            + dim(f"(of {summary['total']})")
        )
    else:
        emit(
            bold("Summary: ")
            + f"{summary['ok']} converted, "
            + f"{summary['empty']} empty, "
            + f"{summary['garbled']} junk, "
            + f"{summary['failed']} failed, "
            + f"{summary['skipped']} skipped "
            + dim(f"(of {summary['total']})")
        )

    if report.output_dir and not dry_run:
        emit(dim(f"Manifest: {report.output_dir / 'manifest.json'}"))

    if summary["empty"]:
        emit("")
        emit(
            warn("Empty results are not written by default: an empty .md looks like a")
        )
        emit(warn("successful conversion downstream. Use --keep-empty to write them anyway."))


def _report_as_dict(report) -> dict:
    files = []
    for item, result in report.all_entries:
        files.append(
            {
                "source": str(item.source),
                "output": str(item.destination) if result.ok else None,
                "verdict": result.verdict.value,
                "chars": result.chars,
                "cause": result.cause or None,
                "fix": result.fix or None,
            }
        )
    for item in report.skipped:
        files.append(
            {
                "source": str(item.source),
                "output": None,
                "verdict": "skipped",
                "chars": 0,
                "cause": item.check.reason or None,
                "fix": item.check.fix or None,
            }
        )
    return {"summary": report.summary(), "files": files}


# --- parser --------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mdc",
        description="Convert files to Markdown with MarkItDown - but tell you why "
        "it failed and what to run next.",
        epilog="Start here:  mdc doctor",
    )
    parser.add_argument("-v", "--version", action="version", version=f"mdc {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_doc = sub.add_parser("doctor", help="check which formats this environment can convert")
    p_doc.set_defaults(func=cmd_doctor)

    p_fmt = sub.add_parser("formats", help="list supported formats and their extras")
    p_fmt.set_defaults(func=cmd_formats)

    p_chk = sub.add_parser("check", help="preflight files without converting them")
    p_chk.add_argument("targets", nargs="+", help="files or directories")
    p_chk.add_argument("-r", "--recursive", action="store_true", help="recurse into subdirectories")
    p_chk.add_argument(
        "--no-install-hint", dest="install_hint", action="store_false",
        help="do not suggest a pip command at the end",
    )
    p_chk.set_defaults(func=cmd_check, install_hint=True)

    p_conv = sub.add_parser("convert", help="convert a file or directory to Markdown")
    p_conv.add_argument("target", help="file or directory")
    p_conv.add_argument("-o", "--output", help="output file or directory")
    p_conv.add_argument(
        "-r", "--recursive", action="store_true", help="recurse into subdirectories"
    )
    p_conv.add_argument(
        "-x", "--extension",
        help="override the extension MarkItDown infers (use when the file is misnamed)",
    )
    p_conv.add_argument(
        "-n", "--dry-run", action="store_true", help="show the plan, convert nothing"
    )
    p_conv.add_argument(
        "--keep-empty", action="store_true",
        help="write .md files even when no text was extracted",
    )
    p_conv.add_argument("-q", "--quiet", action="store_true", help="only print the summary")
    p_conv.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    p_conv.set_defaults(func=cmd_convert)

    return parser


BANNER = """mdc - MarkItDown with a diagnosis

  mdc doctor              what can this environment convert right now?
  mdc formats             full format table with install commands
  mdc check report.pdf    preflight one or more files
  mdc convert docs/ -r    batch convert a directory into .md + manifest.json

Most "it does not work" reports come from a bare `pip install markitdown`,
which cannot read PDF, DOCX, XLSX or PPTX. `mdc doctor` tells you exactly
which extras are missing.
"""


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        emit(BANNER)
        return 0

    try:
        return args.func(args)
    except KeyboardInterrupt:
        emit("")
        emit("Interrupted.")
        return 130
    except BrokenPipeError:
        return 0
    except Exception as exc:
        if os.environ.get("MDC_TRACEBACK"):
            raise
        emit(bad(f"Unexpected error: {type(exc).__name__}: {exc}"))
        emit(dim("Set MDC_TRACEBACK=1 for the full stack trace."))
        return 2


if __name__ == "__main__":
    sys.exit(main())
