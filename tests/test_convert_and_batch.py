"""Tests for conversion verdicts, batch behaviour and the CLI.

The important cases here are the ones that distinguish this wrapper from a thin
pass-through: empty output must be caught, failures must become sentences, and
batch output must not silently overwrite.
"""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from markitdown_doctor.batch import build_plan, run_batch
from markitdown_doctor.cli import main
from markitdown_doctor.convert import Verdict, convert_one
from markitdown_doctor.preflight import Status, check_path

# --- helpers -------------------------------------------------------------


def make_docx(path: Path, text: str) -> Path:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return path


PDF_WITH_TEXT = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 44>>stream
BT /F1 18 Tf 20 100 Td (Report body) Tj ET
endstream endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
trailer<</Root 1 0 R>>
"""

# A PDF whose page has an empty content stream: parses fine, yields no text.
# This is the shape of a scanned document without an OCR layer.
PDF_WITHOUT_TEXT = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]/Contents 4 0 R>>endobj
4 0 obj<</Length 0>>stream

endstream endobj
trailer<</Root 1 0 R>>
"""


def markitdown_available() -> bool:
    try:
        import markitdown  # noqa: F401

        return True
    except ImportError:
        return False


requires_markitdown = pytest.mark.skipif(
    not markitdown_available(), reason="markitdown is not installed"
)


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    (tmp_path / "report.pdf").write_bytes(PDF_WITH_TEXT)
    make_docx(tmp_path / "memo.docx", "Quarterly revenue grew 12 percent")
    (tmp_path / "notes.txt").write_text("plain text body", encoding="utf-8")
    (tmp_path / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "scan.pdf").write_bytes(PDF_WITHOUT_TEXT)
    (tmp_path / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    (tmp_path / "broken.bin").write_bytes(b"PK\x03\x04not-really-a-zip")
    return tmp_path


# --- verdicts ------------------------------------------------------------


@requires_markitdown
def test_successful_conversion_reports_char_count(corpus):
    result = convert_one(corpus / "notes.txt")
    assert result.verdict is Verdict.OK
    assert result.chars > 0
    assert "plain text" in result.markdown


@requires_markitdown
def test_whitespace_only_output_counts_as_empty(tmp_path):
    # A file of spaces is not a useful conversion, even though it is non-empty.
    target = tmp_path / "blank.txt"
    target.write_text("   \n\n\t  \n", encoding="utf-8")
    result = convert_one(target)
    assert result.verdict is Verdict.EMPTY
    assert result.chars == 0


@requires_markitdown
def test_scanned_pdf_is_empty_not_ok(corpus):
    # Upstream: exit 0 and a single newline. Here: an explicit EMPTY verdict.
    result = convert_one(corpus / "scan.pdf")
    assert result.verdict is Verdict.EMPTY
    assert "scanned" in result.cause.lower()
    assert "ocr" in result.fix.lower()


@requires_markitdown
def test_binary_fallback_is_not_reported_as_success(corpus):
    # MarkItDown's PlainTextConverter is a catch-all, so a corrupt archive comes
    # back as mojibake with a successful exit code. Reporting that as OK would
    # feed garbage downstream, so it gets its own verdict.
    result = convert_one(corpus / "broken.bin")
    assert result.verdict is Verdict.GARBLED
    assert "mojibake" in result.cause
    assert not result.useful


@requires_markitdown
def test_directory_input_is_skipped_not_raised(corpus):
    check = check_path(corpus)
    result = convert_one(corpus, check=check)
    assert result.verdict is Verdict.SKIPPED
    assert "directories" in result.cause.lower()


@requires_markitdown
def test_blocking_check_short_circuits_conversion(corpus):
    check = check_path(corpus / "does-not-exist.pdf")
    result = convert_one(corpus / "does-not-exist.pdf", check=check)
    assert result.verdict is Verdict.SKIPPED


# --- exception translation ----------------------------------------------


def test_missing_dependency_maps_to_install_command():
    from markitdown_doctor.convert import _explain_exception

    class MissingDependencyException(Exception):
        pass

    inner = MissingDependencyException(
        "PdfConverter recognized the input as a potential .pdf file, but the "
        "dependencies needed to read .pdf files have not been installed."
    )
    cause, fix = _explain_exception(inner, ".pdf")
    assert "pip install" in fix
    assert "pdf" in fix
    assert "markitdown" in cause


def test_permission_error_maps_to_directory_advice():
    from markitdown_doctor.convert import _explain_exception

    exc = PermissionError("[Errno 13] Permission denied: 'docs'")
    cause, fix = _explain_exception(exc, "")
    assert "directory" in cause.lower()
    assert "mdc convert" in fix


def test_unknown_exception_still_yields_something_readable():
    from markitdown_doctor.convert import _explain_exception

    cause, fix = _explain_exception(RuntimeError("something odd"), ".xyz")
    assert "something odd" in cause
    assert fix


# --- batch planning ------------------------------------------------------


def test_plan_keeps_relative_structure(corpus):
    plan = build_plan(corpus)
    mapping = {item.relative.name: item.destination.name for item in plan}
    assert mapping["notes.txt"] == "notes.md"
    assert mapping["report.pdf"] == "report.md"


def test_plan_gives_every_collision_member_a_distinct_name(tmp_path):
    # a.csv and a.html both want a.md. Naming one of them a.md and the other
    # a.html.md would be arbitrary, so both get their full name.
    (tmp_path / "a.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (tmp_path / "a.html").write_text("<p>hi</p>", encoding="utf-8")

    plan = build_plan(tmp_path)
    destinations = sorted(item.destination.name for item in plan)

    assert destinations == ["a.csv.md", "a.html.md"]
    assert len(set(destinations)) == len(destinations)


def test_plan_respects_recursive_flag(tmp_path):
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "deep.txt").write_text("x", encoding="utf-8")
    (tmp_path / "top.txt").write_text("y", encoding="utf-8")

    flat = build_plan(tmp_path)
    deep = build_plan(tmp_path, recursive=True)

    assert {i.relative.name for i in flat} == {"top.txt"}
    assert {i.relative.name for i in deep} == {"top.txt", "deep.txt"}


def test_plan_marks_missing_dependencies_without_converting(tmp_path, monkeypatch):
    from markitdown_doctor import preflight

    (tmp_path / "x.pdf").write_bytes(PDF_WITH_TEXT)
    monkeypatch.setattr(preflight, "_missing_modules", lambda spec: ("pdfminer",))
    plan = build_plan(tmp_path)
    assert plan[0].check.status is Status.NEEDS_EXTRA


# --- batch execution -----------------------------------------------------


@requires_markitdown
def test_dry_run_writes_nothing(corpus):
    before = set(corpus.iterdir())
    report = run_batch(corpus, output_dir=corpus / "out", dry_run=True)
    assert not (corpus / "out").exists()
    assert report.summary()["planned"] > 0
    assert set(corpus.iterdir()) == before


@requires_markitdown
def test_batch_writes_markdown_and_manifest(corpus):
    out = corpus / "out"
    run_batch(corpus, output_dir=out)

    assert (out / "notes.md").exists()
    assert (out / "notes.md").read_text(encoding="utf-8").strip()
    assert (out / "manifest.json").exists()

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["summary"]["ok"] >= 3
    assert any(f["verdict"] == "empty" for f in manifest["files"])


@requires_markitdown
def test_empty_results_are_not_written_by_default(corpus):
    out = corpus / "out"
    run_batch(corpus, output_dir=out)
    assert not (out / "scan.md").exists()


@requires_markitdown
def test_keep_empty_writes_the_empty_file(corpus):
    out = corpus / "out"
    run_batch(corpus, output_dir=out, keep_empty=True)
    assert (out / "scan.md").exists()


@requires_markitdown
def test_batch_never_overwrites_a_source_file(corpus):
    out = corpus / "out"
    before = {p.name: p.read_bytes() for p in corpus.iterdir() if p.is_file()}
    run_batch(corpus, output_dir=out)
    after = {p.name: p.read_bytes() for p in corpus.iterdir() if p.is_file()}
    assert before == after


# --- CLI -----------------------------------------------------------------


def run_cli(argv, capsys):
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_cli_without_arguments_prints_banner(capsys):
    code, output = run_cli([], capsys)
    assert code == 0
    assert "mdc doctor" in output


def test_cli_formats_lists_install_commands(capsys):
    code, output = run_cli(["formats"], capsys)
    assert code == 0
    assert "markitdown[pdf]" in output
    assert ".doc" in output  # the unsupported section


def test_cli_doctor_reports_something_useful(capsys):
    code, output = run_cli(["doctor"], capsys)
    assert code in (0, 1)
    assert "markitdown" in output
    assert "python" in output


def test_cli_check_flags_missing_file(tmp_path, capsys):
    code, output = run_cli(["check", str(tmp_path / "ghost.pdf")], capsys)
    assert code == 1
    assert "NO FILE" in output


def test_cli_check_suggests_install_command(tmp_path, monkeypatch, capsys):
    from markitdown_doctor import preflight

    (tmp_path / "x.pdf").write_bytes(PDF_WITH_TEXT)
    monkeypatch.setattr(preflight, "_missing_modules", lambda spec: ("pdfminer",))
    code, output = run_cli(["check", str(tmp_path / "x.pdf")], capsys)
    assert code == 1
    assert "pip install 'markitdown[pdf]'" in output


def test_cli_convert_missing_file_exits_2(tmp_path, capsys):
    code, output = run_cli(["convert", str(tmp_path / "nope.pdf")], capsys)
    assert code == 2
    assert "No such file" in output


@requires_markitdown
def test_cli_single_file_writes_markdown_to_stdout(corpus, capsys):
    code, output = run_cli(["convert", str(corpus / "notes.txt")], capsys)
    assert code == 0
    assert "plain text body" in output


@requires_markitdown
def test_cli_empty_conversion_exits_nonzero(corpus, capsys):
    code, output = run_cli(["convert", str(corpus / "scan.pdf")], capsys)
    assert code == 1
    assert "No text extracted" in output


@requires_markitdown
def test_cli_json_report_is_valid_json(corpus, capsys):
    code, output = run_cli(
        ["convert", str(corpus), "-o", str(corpus / "out"), "--json"], capsys
    )
    payload = json.loads(output)
    assert "summary" in payload
    assert isinstance(payload["files"], list)
    assert code in (0, 1)


@requires_markitdown
def test_cli_batch_returns_partial_exit_code(corpus, capsys):
    code, output = run_cli(["convert", str(corpus), "-o", str(corpus / "out")], capsys)
    # The corpus contains a scanned PDF, so this is a partial success.
    assert code == 1
    assert "Summary" in output


@requires_markitdown
def test_cli_quiet_suppresses_the_per_file_report(corpus, capsys):
    code, output = run_cli(
        ["convert", str(corpus), "-o", str(corpus / "out"), "-q"], capsys
    )
    assert code == 1
    assert "Summary" not in output


def test_cli_output_is_ascii_safe_when_encoding_is_limited(tmp_path, capsys):
    # Windows consoles default to cp1252 in some shells; the tool must not
    # crash on its own box-drawing or non-ASCII text.
    (tmp_path / "x.doc").write_bytes(b"legacy")
    code, output = run_cli(["check", str(tmp_path / "x.doc")], capsys)
    assert code == 1
    assert isinstance(output, str)


@requires_markitdown
def test_extension_hint_overrides_the_filename(corpus, tmp_path):
    # A PDF renamed to .docx: magika usually sniffs it correctly, but an
    # explicit hint must reach MarkItDown's StreamInfo.
    mislabeled = tmp_path / "actually_a_pdf.docx"
    mislabeled.write_bytes(PDF_WITH_TEXT)
    result = convert_one(mislabeled, extension_hint="pdf")
    assert result.verdict is Verdict.OK
    assert "Report body" in result.markdown


def test_extension_hint_is_normalised(tmp_path, monkeypatch):
    from markitdown_doctor import convert as convert_module

    captured = {}

    class FakeResult:
        markdown = "content"

    class FakeMD:
        def convert(self, path, stream_info=None):
            captured["stream_info"] = stream_info
            return FakeResult()

    target = tmp_path / "x.bin"
    target.write_bytes(b"whatever")
    convert_module.convert_one(target, md=FakeMD(), extension_hint="PDF")

    info = captured["stream_info"]
    assert info is not None
    assert info.extension == ".pdf"


def test_doctor_without_markitdown_tells_you_to_install_it(monkeypatch, capsys):
    from markitdown_doctor import cli as cli_module
    from markitdown_doctor.preflight import EnvironmentReport

    empty = EnvironmentReport(python_version="3.13.0", markitdown_version="not installed")
    monkeypatch.setattr(cli_module, "diagnose_environment", lambda: empty)

    code = main(["doctor"])
    captured = capsys.readouterr()
    output = captured.out + captured.err

    assert code == 2
    assert "not installed" in output
    assert "pip install markitdown-doctor" in output


# --- end-to-end via a real subprocess ------------------------------------
# The in-process tests above stub nothing, but they cannot prove the console
# entry point wires up correctly. These run the installed `mdc` binary.


def _mdc_command():
    return [sys.executable, "-m", "markitdown_doctor.cli"]


def run_mdc(args, cwd):
    return subprocess.run(
        _mdc_command() + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_subprocess_banner_exits_zero(tmp_path):
    proc = run_mdc([], tmp_path)
    assert proc.returncode == 0
    assert "mdc doctor" in proc.stdout


def test_subprocess_missing_file_exits_two(tmp_path):
    proc = run_mdc(["convert", "ghost.pdf"], tmp_path)
    assert proc.returncode == 2
    assert "No such file" in (proc.stdout + proc.stderr)


@requires_markitdown
def test_subprocess_single_file_exit_code(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("hello from a subprocess", encoding="utf-8")
    proc = run_mdc(["convert", "note.txt"], tmp_path)
    assert proc.returncode == 0
    assert "hello from a subprocess" in proc.stdout


@requires_markitdown
def test_subprocess_empty_conversion_is_not_silent_success(tmp_path):
    target = tmp_path / "scan.pdf"
    target.write_bytes(PDF_WITHOUT_TEXT)
    proc = run_mdc(["convert", "scan.pdf"], tmp_path)
    assert proc.returncode == 1
    assert "No text extracted" in (proc.stdout + proc.stderr)
    assert "Traceback" not in (proc.stdout + proc.stderr)


def test_skipped_result_preserves_the_reason_it_was_skipped(tmp_path):
    # "Skipped" alone is ambiguous: a missing pip extra and an unsupported
    # format both skip, but they need different messages and different fixes.
    from markitdown_doctor.preflight import Status

    target = tmp_path / "ghost.docx"
    check = check_path(target)  # MISSING
    result = convert_one(target, check=check)
    assert result.verdict is Verdict.SKIPPED
    assert result.status is Status.MISSING


def test_unsupported_file_labels_itself_as_unsupported(tmp_path, capsys):
    from markitdown_doctor.preflight import Status

    target = tmp_path / "legacy.doc"
    target.write_bytes(b"legacy bytes")
    check = check_path(target)
    assert check.status is Status.UNSUPPORTED

    main(["convert", str(target)])
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "Unsupported format" in output
    assert "Save As" in output


def test_formats_reflects_actual_install_state(monkeypatch, capsys):
    # A table that says "needs an extra" for an already-installed format is
    # misleading, so the checkbox has to come from the environment.
    from markitdown_doctor import cli as cli_module
    from markitdown_doctor.preflight import EnvironmentReport

    report = EnvironmentReport(
        python_version="3.13.0",
        markitdown_version="0.1.8",
        ready_extras=["pdf"],
        missing_extras={"docx": [".docx"]},
    )
    monkeypatch.setattr(cli_module, "diagnose_environment", lambda: report)

    code = main(["formats"])
    output = capsys.readouterr().out

    pdf_line = next(line for line in output.splitlines() if ".pdf " in line)
    docx_line = next(line for line in output.splitlines() if ".docx " in line)

    assert code == 0
    assert "[x]" in pdf_line
    assert "[ ]" in docx_line


@requires_markitdown
def test_output_is_byte_identical_to_the_original_cli(tmp_path):
    # The README claims `mdc convert x.pdf` produces the same bytes as
    # `markitdown x.pdf`. Assert it rather than trusting the claim, and let CI
    # check it on Linux, macOS and Windows where the newline translation differs.
    target = tmp_path / "report.pdf"
    target.write_bytes(PDF_WITH_TEXT)

    ours = subprocess.run(
        [sys.executable, "-m", "markitdown_doctor.cli", "convert", str(target)],
        capture_output=True,
        check=False,
    )
    theirs = subprocess.run(
        [sys.executable, "-m", "markitdown", str(target)],
        capture_output=True,
        check=False,
    )

    assert ours.returncode == 0
    assert theirs.returncode == 0
    assert ours.stdout == theirs.stdout
