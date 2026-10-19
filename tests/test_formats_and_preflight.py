"""Tests for the format matrix and preflight checks.

These are deliberately about *behaviour users see*: which extra gets suggested,
which status a file gets, whether a blocking status actually blocks.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from markitdown_doctor.formats import (
    BUILTIN_FORMATS,
    EMPTY_RISK_FORMATS,
    EXTRA_FORMATS,
    UNSUPPORTED_FORMATS,
    extensions_by_extra,
    install_command,
    lookup,
)
from markitdown_doctor.preflight import (
    BLOCKING,
    Status,
    check_path,
    collect_inputs,
)

# --- fixtures ------------------------------------------------------------


def make_docx(path: Path, text: str = "hello") -> Path:
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


MINIMAL_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 44>>stream
BT /F1 18 Tf 20 100 Td (Report body) Tj ET
endstream endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
trailer<</Root 1 0 R>>
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "doc.docx").write_bytes(b"")
    make_docx(tmp_path / "doc.docx", "Quarterly report")
    (tmp_path / "paper.pdf").write_bytes(MINIMAL_PDF)
    (tmp_path / "notes.txt").write_text("plain text", encoding="utf-8")
    (tmp_path / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "empty.txt").write_bytes(b"")
    (tmp_path / "legacy.doc").write_bytes(b"\xd0\xcf\x11\xe0legacy")
    (tmp_path / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    return tmp_path


# --- format matrix -------------------------------------------------------


def test_lookup_tolerates_case_and_missing_dot():
    assert lookup("PDF").extension == ".pdf"
    assert lookup("docx").extension == ".docx"
    assert lookup(".Md").extension == ".md"
    assert lookup("") is None
    assert lookup(".nope") is None


def test_every_extra_format_names_an_extra():
    for spec in EXTRA_FORMATS:
        assert spec.extra, f"{spec.extension} must declare the extra that unlocks it"
        assert spec.modules, f"{spec.extension} must declare what to probe"


def test_builtin_formats_need_no_extra():
    for spec in BUILTIN_FORMATS:
        assert spec.extra is None
        assert spec.builtin


def test_no_extension_appears_twice():
    seen = [s.extension for s in BUILTIN_FORMATS + EXTRA_FORMATS]
    assert len(seen) == len(set(seen))


def test_images_are_classified_as_empty_risk_not_unsupported():
    # Images convert "successfully" into nothing, which is different from
    # being unsupported. Conflating the two gives the wrong advice.
    assert ".png" in EMPTY_RISK_FORMATS
    assert ".png" not in UNSUPPORTED_FORMATS


def test_legacy_office_formats_are_unsupported():
    assert ".doc" in UNSUPPORTED_FORMATS
    assert ".ppt" in UNSUPPORTED_FORMATS


def test_install_command_is_narrowest_possible():
    assert install_command(["pdf"]) == "pip install 'markitdown[pdf]'"
    assert install_command(["pdf", "docx"]) == "pip install 'markitdown[pdf,docx]'"
    assert install_command([]) is None
    # Duplicates collapse; order of first appearance wins.
    assert install_command(["docx", "pdf", "docx"]) == "pip install 'markitdown[docx,pdf]'"


def test_extensions_by_extra_covers_all_extras():
    grouped = extensions_by_extra()
    for spec in EXTRA_FORMATS:
        assert spec.extension in grouped[spec.extra]


# --- preflight -----------------------------------------------------------


def test_missing_path_is_blocking(tmp_path):
    check = check_path(tmp_path / "ghost.pdf")
    assert check.status is Status.MISSING
    assert check.blocking


def test_directory_is_reported_as_directory_not_permission_error(workspace):
    # Upstream raises PermissionError [Errno 13] here, which sends users
    # looking for a permissions problem that does not exist.
    check = check_path(workspace)
    assert check.status is Status.IS_DIRECTORY
    assert "PermissionError" in check.reason
    assert "mdc convert" in check.fix


def test_zero_byte_file_is_blocking(workspace):
    check = check_path(workspace / "empty.txt")
    assert check.status is Status.EMPTY_FILE
    assert check.blocking


def test_builtin_format_is_ready(workspace):
    check = check_path(workspace / "notes.txt")
    assert check.status is Status.READY
    assert not check.blocking


def test_unsupported_format_explains_the_workaround(workspace):
    check = check_path(workspace / "legacy.doc")
    assert check.status is Status.UNSUPPORTED
    assert ".docx" in check.fix


def test_image_without_exiftool_is_flagged_before_conversion(workspace, monkeypatch):
    import shutil as shutil_module

    monkeypatch.setattr(shutil_module, "which", lambda name: None)
    check = check_path(workspace / "photo.png")
    assert check.status is Status.LIKELY_EMPTY
    assert check.blocking
    assert "empty" in check.reason.lower()


def test_missing_extra_produces_runnable_command(workspace, monkeypatch):
    from markitdown_doctor import preflight

    # Simulate a bare `pip install markitdown`: pretend the PDF libs are absent.
    monkeypatch.setattr(
        preflight, "_missing_modules", lambda spec: ("pdfminer", "pdfplumber")
        if spec.extension == ".pdf" else ()
    )
    check = check_path(workspace / "paper.pdf")
    assert check.status is Status.NEEDS_EXTRA
    assert check.fix == "pip install 'markitdown[pdf]'"
    assert "pdfminer" in check.reason


def test_ready_when_dependencies_present(workspace, monkeypatch):
    from markitdown_doctor import preflight

    monkeypatch.setattr(preflight, "_missing_modules", lambda spec: ())
    check = check_path(workspace / "paper.pdf")
    assert check.status is Status.READY
    assert not check.blocking


def test_blocking_set_covers_every_failure_status():
    assert Status.NEEDS_EXTRA in BLOCKING
    assert Status.UNSUPPORTED in BLOCKING
    assert Status.LIKELY_EMPTY in BLOCKING
    assert Status.MISSING in BLOCKING
    assert Status.IS_DIRECTORY in BLOCKING
    assert Status.EMPTY_FILE in BLOCKING
    # READY and UNKNOWN must never block: UNKNOWN may well convert fine.
    assert Status.READY not in BLOCKING
    assert Status.UNKNOWN not in BLOCKING


# --- input collection ----------------------------------------------------


def test_collect_inputs_is_sorted_and_skips_directories(workspace):
    files = collect_inputs(workspace)
    names = [f.name for f in files]
    assert names == sorted(names)
    assert all(f.is_file() for f in files)


def test_collect_inputs_recurses_only_when_asked(workspace):
    nested = workspace / "sub"
    nested.mkdir()
    (nested / "deep.txt").write_text("x", encoding="utf-8")

    shallow = [f.name for f in collect_inputs(workspace)]
    deep = [f.name for f in collect_inputs(workspace, recursive=True)]

    assert "deep.txt" not in shallow
    assert "deep.txt" in deep


def test_collect_inputs_passes_through_a_single_file(workspace):
    target = workspace / "notes.txt"
    assert collect_inputs(target) == [target]
