# Changelog

## 0.1.0

First release. A diagnostic wrapper around MarkItDown 0.1.8.

### Added

- `mdc doctor` — reports which MarkItDown extras this interpreter has, and
  prints the narrowest `pip install` command that closes the gaps.
- `mdc formats` — the full capability table, split into three groups: works on a
  bare install, needs an extra, and accepted-but-produces-nothing.
- `mdc check` — preflight one or many paths without converting. Uses only
  metadata reads and `importlib` probes.
- `mdc convert` — batch conversion of a directory into `.md` files plus a
  `manifest.json` recording a verdict, character count and cause for every input.
- `--json` machine-readable report, `-n/--dry-run`, `--keep-empty`, `-x` extension
  override, and `-q`.
- Exit codes: `0` all good, `1` partial, `2` nothing converted.

### Fixed relative to calling MarkItDown directly

- **Empty output no longer looks like success.** A scanned PDF or an image with
  no OCR backend returns exit code 0 and an empty string upstream. Here it is an
  `empty` verdict, is not written to disk, and carries an OCR hint.
- **Binary fallback is caught.** When no converter claims a file, MarkItDown's
  catch-all text converter returns mojibake as a successful conversion. Here it
  is a `junk` verdict.
- **Directory input no longer surfaces `PermissionError [Errno 13]`**, which
  reads like a permissions bug. It is reported as a directory, with the batch
  command to use instead.
- **Exceptions become cause + fix.** `MissingDependencyException` nested inside
  `FileConversionException` is unwrapped and translated into the pip command
  that resolves it.
- **No traceback by default.** `MDC_TRACEBACK=1` restores the full stack.

### Compatibility

- Single-file stdout output is byte-identical to `markitdown <file>`, verified
  with `cmp` on both an ASCII PDF and a UTF-8 DOCX.
- No MarkItDown source is modified, vendored or monkey-patched.
