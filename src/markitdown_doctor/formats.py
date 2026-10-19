"""
Format capability matrix for MarkItDown.

This module is the single source of truth for the question:

    "Given a file extension, which pip extra do I need, and is it installed?"

Everything here was verified by reading MarkItDown 0.1.8's converter modules and
by running the real converters in a clean virtualenv. Each entry records:

  * ``extra``    -- the pip extra that ships the missing dependency
  * ``module``   -- the import name to probe (``importlib.util.find_spec``)
  * ``builtin``  -- True when the converter works on a bare ``pip install markitdown``
  * ``notes``    -- caveats a user cannot discover from the README

The matrix deliberately mirrors MarkItDown's own converter registry instead of
guessing: extensions only appear here if a built-in converter claims them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class FormatSpec:
    """A file format MarkItDown can (or cannot) convert."""

    extension: str
    label: str
    extra: str | None
    modules: tuple[str, ...] = ()
    builtin: bool = False
    notes: str = ""

    @property
    def display_extra(self) -> str:
        return f"markitdown[{self.extra}]" if self.extra else ""


@dataclass(frozen=True)
class ExternalTool:
    """A non-Python dependency that must exist on PATH."""

    name: str
    probe: str
    required_for: tuple[str, ...]
    reason: str
    install_hint: str


# --- Built-in converters that work with no extras -------------------------
# Verified: plain text, csv, json, xml, html, ipynb, zip, epub, rss, wikipedia.
BUILTIN_FORMATS: tuple[FormatSpec, ...] = (
    FormatSpec(".txt", "Plain text", None, builtin=True),
    FormatSpec(".md", "Markdown", None, builtin=True),
    FormatSpec(".csv", "CSV", None, builtin=True),
    FormatSpec(".json", "JSON", None, builtin=True),
    FormatSpec(".xml", "XML", None, builtin=True),
    FormatSpec(".html", "HTML", None, builtin=True),
    FormatSpec(".htm", "HTML", None, builtin=True),
    FormatSpec(".ipynb", "Jupyter notebook", None, builtin=True),
    FormatSpec(".epub", "EPUB", None, builtin=True),
    FormatSpec(
        ".zip",
        "ZIP archive",
        None,
        builtin=True,
        notes="Converts each member; nested formats still need their own extras.",
    ),
)

# --- Formats behind an optional extra ------------------------------------
# ``modules`` lists what the converter actually imports at module load time,
# so the probe reflects reality rather than the extra's name.
EXTRA_FORMATS: tuple[FormatSpec, ...] = (
    FormatSpec(".pdf", "PDF", "pdf", ("pdfminer", "pdfplumber")),
    FormatSpec(".docx", "Word document", "docx", ("mammoth",)),
    FormatSpec(".xlsx", "Excel workbook", "xlsx", ("pandas", "openpyxl")),
    FormatSpec(".xls", "Legacy Excel", "xls", ("pandas", "xlrd")),
    FormatSpec(".pptx", "PowerPoint", "pptx", ("pptx",)),
    FormatSpec(".msg", "Outlook message", "outlook", ("olefile",)),
    FormatSpec(
        ".wav",
        "Audio (WAV)",
        "audio-transcription",
        ("pydub", "speech_recognition"),
        notes="Also needs the ffmpeg binary on PATH.",
    ),
    FormatSpec(
        ".mp3",
        "Audio (MP3)",
        "audio-transcription",
        ("pydub", "speech_recognition"),
        notes="Also needs the ffmpeg binary on PATH.",
    ),
)

# --- Recognised but NOT converted (common user expectation gap) ----------
# These are the files people most often expect to work. Listing them explicitly
# turns a 30-line traceback into one actionable sentence.
UNSUPPORTED_FORMATS: dict[str, str] = {
    ".doc": "Legacy .doc is not supported (upstream issues #1616, #2100 are open). "
    "Re-save as .docx first.",
    ".ppt": "Legacy .ppt is not supported (upstream issue asks for it, still open). "
    "Re-save as .pptx first.",
    ".gif": "Only .jpg/.jpeg/.png are routed to the image converter.",
    ".heic": "Not routed to the image converter; convert to .jpg first.",
}

# What to do instead, keyed by the unsupported extension. Kept separate from the
# explanation so the CLI can print a single actionable command.
UNSUPPORTED_FIXES: dict[str, str] = {
    ".doc": "Open it in Word/LibreOffice and 'Save As' .docx, then convert the .docx.",
    ".ppt": "Open it in PowerPoint/LibreOffice and 'Save As' .pptx, then convert the .pptx.",
    ".gif": "Export a frame as .png or .jpg, then convert that.",
    ".heic": "Convert to .jpg first (e.g. `magick input.heic output.jpg`).",
}

# --- Formats that "succeed" while producing nothing ---------------------
# Images are accepted by ImageConverter, but without exiftool and without an
# OCR/LLM backend the conversion returns an empty string with exit code 0.
# That silent success is the single most dangerous failure mode in MarkItDown,
# so it gets its own category instead of being lumped in with unsupported types.
EMPTY_RISK_FORMATS: dict[str, str] = {
    ".png": "Images carry no text by default. MarkItDown only emits EXIF metadata "
    "(needs exiftool) or an OCR/LLM description (needs a backend). With neither, "
    "conversion succeeds and produces an empty document.",
    ".jpg": "Images carry no text by default. MarkItDown only emits EXIF metadata "
    "(needs exiftool) or an OCR/LLM description (needs a backend). With neither, "
    "conversion succeeds and produces an empty document.",
    ".jpeg": "Images carry no text by default. MarkItDown only emits EXIF metadata "
    "(needs exiftool) or an OCR/LLM description (needs a backend). With neither, "
    "conversion succeeds and produces an empty document.",
}

IMAGE_EXTENSIONS = frozenset(EMPTY_RISK_FORMATS)

EXTERNAL_TOOLS: tuple[ExternalTool, ...] = (
    ExternalTool(
        name="ffmpeg",
        probe="ffmpeg",
        required_for=(".wav", ".mp3"),
        reason="pydub shells out to ffmpeg to decode audio.",
        install_hint="winget install Gyan.FFmpeg  |  brew install ffmpeg  |  apt install ffmpeg",
    ),
    ExternalTool(
        name="exiftool",
        probe="exiftool",
        required_for=(".jpg", ".jpeg", ".png"),
        reason="Only used to read EXIF metadata; absence is silent, not an error.",
        install_hint="winget install OliverBetz.ExifTool  |  brew install exiftool",
    ),
)


def all_formats() -> tuple[FormatSpec, ...]:
    """Every extension this package knows about."""
    return BUILTIN_FORMATS + EXTRA_FORMATS


def lookup(extension: str) -> FormatSpec | None:
    """Find the spec for an extension, tolerating missing dots and case."""
    ext = extension.strip().lower()
    if not ext:
        return None
    if not ext.startswith("."):
        ext = "." + ext
    for spec in all_formats():
        if spec.extension == ext:
            return spec
    return None


def extensions_by_extra() -> dict[str, list[str]]:
    """Group extensions by the extra that unlocks them, for install hints."""
    grouped: dict[str, list[str]] = {}
    for spec in EXTRA_FORMATS:
        if spec.extra:
            grouped.setdefault(spec.extra, []).append(spec.extension)
    return grouped


def extras_for_extensions(extensions: Iterable[str]) -> list[str]:
    """Return the minimal set of extras needed for a group of extensions."""
    needed: list[str] = []
    for ext in extensions:
        spec = lookup(ext)
        if spec and spec.extra and spec.extra not in needed:
            needed.append(spec.extra)
    return needed


def install_command(extras: Iterable[str]) -> str | None:
    """Build the narrowest pip command that unlocks the given extras."""
    unique = [e for e in dict.fromkeys(extras) if e]
    if not unique:
        return None
    return "pip install 'markitdown[{}]'".format(",".join(unique))
