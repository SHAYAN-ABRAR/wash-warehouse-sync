"""``wash-sync`` command-line interface.

Daily use is through ``wash-sync serve`` (local web app). Every operation is also
available here for scripting and for environments without a browser.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from contextlib import contextmanager
from typing import Any, Iterator, Optional

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from . import __version__
from .config import Settings, default_state_dir
from .engine import Engine, EngineError, plan_summary
from .planner import PreviewInput

app = typer.Typer(add_completion=False, no_args_is_help=True, help="WASH Warehouse Sync — incremental, auditable "
                  "synchronization of the single-sheet WASH warehouse.")
review_app = typer.Typer(no_args_is_help=True, help="List and decide review items of a plan.")
app.add_typer(review_app, name="review")
console = Console()
StateOpt = typer.Option(None, "--state-dir", envvar="WASH_SYNC_HOME", help="Private state directory (default ~/.wash_sync)")


def _actor() -> str:
    try:
        return os.environ.get("WASH_SYNC_ACTOR") or getpass.getuser()
    except Exception:  # noqa: BLE001
        return "local-user"


def _engine(state_dir: Optional[str]) -> Engine:
    return Engine(Settings.load(state_dir or default_state_dir()), state_dir or default_state_dir())


@contextmanager
def _progress() -> Iterator[Any]:
    with Progress(TextColumn("[bold cyan]{task.fields[phase]:>8}"), TextColumn("{task.description}"), BarColumn(),
                  TaskProgressColumn(), TimeElapsedColumn(), console=console, transient=False) as prog:
        task = prog.add_task("starting", total=1.0, phase="")

        def update(phase: str, fraction: float, message: str) -> None:
            prog.update(task, completed=fraction, description=message, phase=phase)

        yield update


def _fail(exc: Exception) -> None:
    console.print(f"[bold red]Error:[/] {exc}")
    raise typer.Exit(code=2)


@app.command()
def version() -> None:
    """Print the engine version."""
    console.print(f"wash-sync {__version__}")


@app.command()
def init(warehouse: str = typer.Option(..., "--warehouse", "-w", help="Verified warehouse .xlsx to register as v000"),
         state_dir: Optional[str] = StateOpt, force: bool = typer.Option(False, help="Register even with warnings")) -> None:
    """Register the existing verified warehouse as the immutable baseline (copied into private state)."""
    eng = _engine(state_dir)
    try:
        with _progress() as update:
            record = eng.init(warehouse, actor=_actor(), force=force, progress=update)
    except EngineError as exc:
        _fail(exc)
    console.print(f"[green]Baseline registered[/] as {record['file']} — {record['rows']:,} rows × {record['columns']} columns")
    warn = [c for c in record["validation"]["checks"] if c["status"] != "PASS"]
    for c in warn:
        console.print(f"  [yellow]{c['status']}[/] {c['name']}: {c['detail']}")


@app.command()
def inspect(files: list[str] = typer.Argument(..., help="Source exports (.xlsx, .xls, .csv)"),
            source: Optional[str] = typer.Option(None, help="GPANI | WPMS | AQUAWATCH | BORELOG (default: auto)"),
            state_dir: Optional[str] = StateOpt, as_json: bool = typer.Option(False, "--json")) -> None:
    """Detect each file's source system, format and column mapping (read-only)."""
    results = _engine(state_dir).inspect(files, source)
    if as_json:
        console.print_json(json.dumps(results, default=str))
        return
    for r in results:
        color = "green" if r["source"] and not r["missing_required"] else "red"
        console.print(f"[bold]{r['file']}[/] → [{color}]{r['source'] or 'UNRECOGNIZED'}[/] {r['format'] or ''} "
                      f"(confidence {r['confidence']:.2f}, ~{r['estimated_rows'] or '?'} rows)")
        console.print(f"   {r['reason']}")
        if r["missing_required"]:
            console.print(f"   [red]Missing required:[/] {r['missing_required']}")
        if r["personal_data_columns"]:
            console.print(f"   [yellow]Personal-data columns ignored:[/] {', '.join(r['personal_data_columns'])}")
        if r["ignored_columns"]:
            console.print(f"   Unmapped columns: {', '.join(r['ignored_columns'])}")


@app.command()
def preview(files: list[str] = typer.Argument(..., help="Source exports to compare with the current warehouse"),
            source: Optional[str] = typer.Option(None, help="Force the source system for all files"),
            period: Optional[str] = typer.Option(None, help="Expected AquaWatch period, e.g. '2027 dry' or '2027'"),
            strict: bool = typer.Option(False, help="Strict validation mode for this run"),
            state_dir: Optional[str] = StateOpt) -> None:
    """Dry run: classify every record and write a plan + audit. Nothing is written to the warehouse."""
    eng = _engine(state_dir)
    if strict:
        eng.settings.validation.mode = "strict"
    try:
        with _progress() as update:
            plan = eng.preview([PreviewInput(path=f, source=source, period=period) for f in files], _actor(), update)
    except (EngineError, ValueError) as exc:
        _fail(exc)
    _print_plan(plan)


def _print_plan(plan: Any) -> None:
    table = Table(title=f"Plan {plan.plan_id} (base v{plan.base_version:03d}) — {plan.status}")
    table.add_column("Classification")
    table.add_column("Records", justify="right")
    for k, v in plan.summary["by_classification"].items():
        if v:
            table.add_row(k, f"{v:,}")
    console.print(table)
    for s in plan.sources:
        console.print(f"  {s.file_name}: {s.source} {s.format} — {s.records:,} records {s.periods or ''}")
    if plan.new_columns:
        console.print(f"  New columns: {', '.join(c.name for c in plan.new_columns)}")
    console.print(f"  Review items: {len(plan.review_items):,} ({len(plan.undecided):,} undecided)")
    console.print(f"  Audit: {plan.audit_dir}")
    console.print(f"Next: [bold]wash-sync review list {plan.plan_id}[/], then [bold]wash-sync approve {plan.plan_id}[/] "
                  f"and [bold]wash-sync apply {plan.plan_id}[/]")


@review_app.command("list")
def review_list(plan_id: str, state_dir: Optional[str] = StateOpt, undecided_only: bool = False,
                limit: int = 50) -> None:
    """List review items with their options."""
    plan = _engine(state_dir).plan(plan_id)
    shown = 0
    for item in plan.review_items:
        if undecided_only and item.decision:
            continue
        shown += 1
        if shown > limit:
            console.print(f"... {len(plan.review_items) - limit} more")
            break
        state = f"[green]{item.decision.choice}[/]" if item.decision else "[yellow]UNDECIDED[/]"
        console.print(f"[bold]{item.item_id}[/] {item.classification.value} {state}: {item.title}")
        console.print(f"    {item.reason}")
        for i, opt in enumerate(item.options):
            mark = " (recommended)" if item.recommended == i else ""
            console.print(f"    [{i}] {opt.decision}: {opt.label}{mark}")


@review_app.command("decide")
def review_decide(plan_id: str, item_id: str, decision: str = typer.Option(..., "--decision", "-d"),
                  reason: str = typer.Option(..., "--reason", "-r", help="Required explanation"),
                  option: Optional[int] = typer.Option(None, help="Option index when several share a decision"),
                  state_dir: Optional[str] = StateOpt) -> None:
    """Record a decision (APPROVE_MATCH, KEEP_SEPARATE, ACCEPT_NEW_VALUES, KEEP_EXISTING, ACCEPT, REJECT, DEFER)."""
    try:
        result = _engine(state_dir).decide(plan_id, item_id, decision.upper(), reason, _actor(), option)
    except EngineError as exc:
        _fail(exc)
    console.print(f"[green]Recorded[/] {result}")


@review_app.command("bulk")
def review_bulk(plan_id: str, classification: str = typer.Option(..., "--class"),
                decision: str = typer.Option(..., "--decision", "-d"), reason: str = typer.Option(..., "--reason", "-r"),
                state_dir: Optional[str] = StateOpt) -> None:
    """Apply one decision to every undecided item of a classification."""
    n = _engine(state_dir).bulk_decide(plan_id, classification.upper(), decision.upper(), reason, _actor())
    console.print(f"[green]{n} item(s) decided[/]")


@app.command()
def approve(plan_id: str, note: Optional[str] = None, state_dir: Optional[str] = StateOpt) -> None:
    """Approve a plan. Undecided review items will be deferred (never auto-approved)."""
    try:
        plan = _engine(state_dir).approve(plan_id, _actor(), note)
    except EngineError as exc:
        _fail(exc)
    console.print(f"[green]Approved[/] {plan.plan_id}; {len(plan.undecided)} undecided item(s) will be deferred")


@app.command()
def apply(plan_id: str, yes: bool = typer.Option(False, "--yes", help="Explicitly authorize an unapproved plan"),
          require_all_decided: bool = typer.Option(False, help="Fail if any review item is undecided"),
          force_export: bool = typer.Option(False, help="Write a new version even when nothing changes"),
          state_dir: Optional[str] = StateOpt) -> None:
    """Apply an approved plan: write → verify → atomically publish the next version."""
    eng = _engine(state_dir)
    try:
        with _progress() as update:
            result = eng.apply(plan_id, _actor(), authorize=yes, defer_unresolved=not require_all_decided,
                               force_export=force_export, progress=update)
    except EngineError as exc:
        _fail(exc)
    if result.get("new_version") is None:
        console.print(f"[yellow]{result['message']}[/]")
        return
    console.print(f"[green]Published[/] {result['file']} ({result['rows']:,} rows × {result['columns']} columns)")
    console.print_json(json.dumps(result["counts"]))
    console.print(f"Audit: {result['audit_dir']}")


@app.command()
def verify(version_number: Optional[int] = typer.Option(None, "--version"), file: Optional[str] = None,
           quick: bool = typer.Option(False, help="Skip CRC and full XML parse"), state_dir: Optional[str] = StateOpt) -> None:
    """Verify a warehouse version (ZIP, XML, single sheet, ranges, ordering, independent reader)."""
    eng = _engine(state_dir)
    try:
        with _progress() as update:
            report = eng.verify(version_number, file, deep=not quick, progress=lambda f: update("verify", f, "Verifying"))
    except (EngineError, KeyError) as exc:
        _fail(exc)
    for c in report["checks"]:
        color = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}[c["status"]]
        console.print(f"[{color}]{c['status']:4}[/] {c['name']}: {c['detail']}")
    console.print(("[green]OK[/]" if report["ok"] else "[red]FAILED[/]") + f" — {report['stats'].get('data_rows', 0):,} data rows")
    if not report["ok"]:
        raise typer.Exit(code=1)


@app.command()
def history(state_dir: Optional[str] = StateOpt, events: int = 15) -> None:
    """Show warehouse versions and recent events."""
    h = _engine(state_dir).history()
    table = Table(title=f"Warehouse versions (current: v{h['current_version']:03d})" if h["current_version"] is not None
                  else "No versions yet")
    for col in ("Version", "Kind", "Created", "Rows", "Cols", "Added", "Updated", "Plan"):
        table.add_column(col)
    for v in h["versions"]:
        c = v.get("counts", {})
        mark = " ◀" if v["version"] == h["current_version"] else ""
        table.add_row(f"v{v['version']:03d}{mark}", v.get("kind", ""), v["created_at"], f"{v.get('rows') or 0:,}",
                      str(v.get("columns")), str(c.get("added_facilities", "")), str(c.get("updated_facilities", "")),
                      v.get("plan_id") or "")
    console.print(table)
    for e in h["events"][-events:]:
        console.print(f"  {e['at']}  {e['event']:<18} {json.dumps({k: v for k, v in e.items() if k not in ('at', 'event')}, default=str)[:160]}")


@app.command()
def rollback(to: int = typer.Option(..., "--to", help="Version number to make current"),
             reason: str = typer.Option(..., "--reason", "-r"), state_dir: Optional[str] = StateOpt) -> None:
    """Make an earlier verified version current again (no files are deleted)."""
    try:
        result = _engine(state_dir).rollback(to, _actor(), reason)
    except (EngineError, KeyError) as exc:
        _fail(exc)
    console.print(f"[green]Current version is now v{result['current_version']:03d}[/] ({result['note']})")


@app.command()
def publish(version_number: Optional[int] = typer.Option(None, "--version"),
            confirm: bool = typer.Option(False, "--confirm", help="Required: confirm copying to private storage"),
            state_dir: Optional[str] = StateOpt) -> None:
    """Copy a verified version to the configured private storage destination."""
    try:
        result = _engine(state_dir).publish(version_number, _actor(), confirm)
    except (EngineError, KeyError) as exc:
        _fail(exc)
    console.print(f"[green]Published[/] {result['published']} to {result['destination']}")


@app.command()
def recover(state_dir: Optional[str] = StateOpt) -> None:
    """Clean up after an interrupted apply. The verified warehouse is never modified."""
    console.print_json(json.dumps(_engine(state_dir).recover()))


@app.command()
def plans(state_dir: Optional[str] = StateOpt) -> None:
    """List saved plans."""
    eng = _engine(state_dir)
    for plan_id in eng.store.list_plans():
        s = plan_summary(eng.plan(plan_id))
        console.print(f"{s['plan_id']}  {s['status']:<10} base v{s['base_version']}  files={s['files']}  "
                      f"review={s['review_items']} (undecided {s['undecided']})")


@app.command()
def serve(host: str = typer.Option("127.0.0.1", help="Bind address (local only by default)"),
          port: int = 8765, open_browser: bool = typer.Option(True, "--open/--no-open"),
          state_dir: Optional[str] = StateOpt) -> None:
    """Start the local production app (API + web interface) on this machine."""
    try:
        from wash_sync_api.server import run  # noqa: PLC0415
    except ImportError as exc:
        console.print("[red]The local server needs the optional dependencies:[/] pip install -e '.[server]'")
        raise typer.Exit(code=2) from exc
    run(host=host, port=port, state_dir=state_dir or default_state_dir(), open_browser=open_browser)


@app.command()
def synth(out: str = typer.Option(..., help="Output .xlsx path"), rows: int = 600_000, columns: int = 196,
          seed: int = 7) -> None:
    """Generate a large SYNTHETIC warehouse for scale testing (no real data)."""
    from .synthetic import write_synthetic_warehouse  # noqa: PLC0415

    with _progress() as update:
        info = write_synthetic_warehouse(out, rows=rows, columns=columns, seed=seed,
                                         progress=lambda f: update("synth", f, f"Generating {rows:,} rows"))
    console.print(f"[green]Wrote[/] {out}: {info['rows']:,} rows × {info['columns']} columns")


@app.command("demo-data")
def demo_data(out: str = typer.Option("apps/web/public/demo", help="Output folder for the web demo bundle"),
              seed: int = 2026) -> None:
    """Build the synthetic demo bundle used by the GitHub Pages site (runs the real engine)."""
    from .demo import build_demo_bundle  # noqa: PLC0415

    info = build_demo_bundle(out, seed=seed, log=lambda m: console.print(f"  {m}"))
    console.print(f"[green]Demo bundle written[/] to {out}: {info}")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted. The verified warehouse is unchanged; run `wash-sync recover` if needed.[/]")
        sys.exit(130)


if __name__ == "__main__":
    main()
