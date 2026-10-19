"""
Batch conversion: turn a directory into a folder of .md files, with a manifest.

Upstream has four open requests for this (#1371, #1372, #1825, #2353). Doing it
here rather than in core keeps MarkItDown untouched, and lets the manifest carry
the verdict for every file -- which is what makes silent empty output visible in
a batch run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .convert import Result, Verdict, convert_one
from .preflight import Check, check_path, collect_inputs


@dataclass
class PlanItem:
    """One input file and where its Markdown would land."""

    source: Path
    relative: Path
    destination: Path
    check: Check


@dataclass
class BatchReport:
    """Everything a caller needs to judge a batch run."""

    items: list[tuple[PlanItem, Result]] = field(default_factory=list)
    skipped: list[PlanItem] = field(default_factory=list)
    planned: list[PlanItem] = field(default_factory=list)
    output_dir: Path | None = None
    dry_run: bool = False

    @property
    def converted(self) -> list[tuple[PlanItem, Result]]:
        return [(p, r) for p, r in self.items if r.verdict is Verdict.OK]

    @property
    def empty(self) -> list[tuple[PlanItem, Result]]:
        return [(p, r) for p, r in self.items if r.verdict is Verdict.EMPTY]

    @property
    def failed(self) -> list[tuple[PlanItem, Result]]:
        return [(p, r) for p, r in self.items if r.verdict is Verdict.FAILED]

    @property
    def garbled(self) -> list[tuple[PlanItem, Result]]:
        return [(p, r) for p, r in self.items if r.verdict is Verdict.GARBLED]

    @property
    def all_entries(self) -> list[tuple[PlanItem, Result]]:
        """Converted, empty, garbled and failed, in input order."""
        return self.items

    def summary(self) -> dict[str, int]:
        total = len(self.items) + len(self.skipped) + len(self.planned)
        return {
            "total": total,
            "ok": len(self.converted),
            "empty": len(self.empty),
            "garbled": len(self.garbled),
            "failed": len(self.failed),
            "skipped": len(self.skipped),
            "planned": len(self.planned),
        }


def _safe_stem(relative: Path) -> Path:
    """Map ``docs/report.pdf`` to ``docs/report.md``."""
    return relative.with_suffix(".md")


def build_plan(
    target: str | Path,
    *,
    recursive: bool = False,
    output_dir: str | Path | None = None,
) -> list[PlanItem]:
    """Decide, before any conversion, what will be attempted and where it lands.

    When several sources map to the same ``stem.md`` (``a.csv`` and ``a.html``
    both want ``a.md``), *every* member of that collision group gets its full
    filename plus ``.md`` (``a.csv.md``, ``a.html.md``). Naming the first one
    ``a.md`` and the rest ``a.html.md`` would be arbitrary and make output
    directories hard to reason about.
    """
    root = Path(target)
    base = root if root.is_dir() else root.parent
    out_root = Path(output_dir) if output_dir else base

    files = collect_inputs(root, recursive=recursive)

    relatives: list[tuple[Path, Path]] = []
    for f in files:
        try:
            relative = f.relative_to(base)
        except ValueError:
            relative = Path(f.name)
        relatives.append((f, relative))

    # Count how many sources want each destination stem.
    demand: dict[Path, int] = {}
    for _, relative in relatives:
        demand[_safe_stem(relative)] = demand.get(_safe_stem(relative), 0) + 1

    plan: list[PlanItem] = []
    for f, relative in relatives:
        stem_dest = _safe_stem(relative)
        if demand[stem_dest] > 1:
            destination = out_root / (str(relative) + ".md")
        else:
            destination = out_root / stem_dest

        plan.append(
            PlanItem(
                source=f,
                relative=relative,
                destination=destination,
                check=check_path(f),
            )
        )

    return plan


def run_batch(
    target: str | Path,
    *,
    recursive: bool = False,
    output_dir: str | Path | None = None,
    dry_run: bool = False,
    keep_empty: bool = False,
    on_progress=None,
) -> BatchReport:
    """Convert every file under ``target`` and write a manifest.

    Args:
        dry_run: build and report the plan without writing anything.
        keep_empty: write .md files even when the verdict is EMPTY. Off by
            default, because an empty .md is worse than no .md -- it looks
            like a successful conversion downstream.
        on_progress: optional ``callable(plan_item, result)`` for CLI feedback.
    """
    plan = build_plan(target, recursive=recursive, output_dir=output_dir)
    report = BatchReport(
        output_dir=Path(output_dir) if output_dir else None,
        dry_run=dry_run,
    )

    if dry_run:
        # A dry run separates "would convert" from "would be skipped" so the
        # summary is readable as a prediction, not as seven failures.
        report.planned = [i for i in plan if not i.check.blocking]
        report.skipped = [i for i in plan if i.check.blocking]
        return report

    md = None
    for item in plan:
        if item.check.blocking:
            report.skipped.append(item)
            if on_progress:
                on_progress(item, None)
            continue

        if md is None:
            from markitdown import MarkItDown

            md = MarkItDown()

        result = convert_one(item.source, md=md, check=item.check)
        report.items.append((item, result))

        should_write = result.verdict is Verdict.OK or (
            keep_empty and result.verdict is Verdict.EMPTY
        )
        if should_write:
            item.destination.parent.mkdir(parents=True, exist_ok=True)
            item.destination.write_text(result.markdown, encoding="utf-8")

        if on_progress:
            on_progress(item, result)

    if report.output_dir:
        _write_manifest(report, report.output_dir / "manifest.json")

    return report

def _write_manifest(report: BatchReport, path: Path) -> None:
    """Persist per-file verdicts so a batch run is auditable later."""
    entries = []
    for item, result in report.all_entries:
        entries.append(
            {
                "source": str(item.source),
                "output": str(item.destination) if result.ok else None,
                "verdict": result.verdict.value,
                "chars": result.chars,
                "elapsed_ms": result.elapsed_ms,
                "cause": result.cause or None,
                "fix": result.fix or None,
            }
        )
    for item in report.skipped:
        entries.append(
            {
                "source": str(item.source),
                "output": None,
                "verdict": "skipped",
                "chars": 0,
                "elapsed_ms": 0,
                "cause": item.check.reason or None,
                "fix": item.check.fix or None,
            }
        )

    payload = {"summary": report.summary(), "files": entries}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
