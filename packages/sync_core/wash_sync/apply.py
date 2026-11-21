"""Apply an approved plan: write, verify and atomically publish a new version.

Safety properties
-----------------
* Refuses unless the plan is approved (unchanged since approval) or explicitly
  authorized, and unless the current version is exactly the plan's base version.
* The base workbook is read-only input. Output is written under ``tmp/``, fully
  verified (integrity + cell-level preservation proof) and only then moved into
  ``versions/`` with an atomic rename. Any failure or cancellation leaves the
  verified warehouse untouched and removes the partial file.
* A ``FILL`` is applied only when the target cell is blank; overwriting requires a
  decided review option (``SET``).
"""

from __future__ import annotations

import collections
import json
import os
import shutil
from collections.abc import Callable
from typing import Any

from . import SCHEMA_VERSION, __version__
from .audit import AuditWriter
from .changes import is_blank, values_equal
from .config import Settings
from .index import WarehouseIndex, file_sha256
from .plan import Operation, Plan
from .records import ChangeClass
from .schema import final_column_order, parse_period_column
from .state import StateStore, atomic_write_json, new_id, now_iso
from .text import join_tokens, split_tokens
from .xlsx.verify import verify_against_base, verify_workbook
from .xlsx.writer import CellWrite, NewRow, OperationCancelled, RewriteSpec, rewrite_workbook

ProgressFn = Callable[[str, float, str], None]


class ApplyError(RuntimeError):
    pass


def _state_path(store: StateStore, plan_id: str) -> str:
    return store.plan_path(plan_id)[: -len(".json")] + ".state.json"


def load_plan(store: StateStore, plan_id: str) -> Plan:
    """Load a plan: the immutable preview document plus its small mutable sidecar.

    Large plans (tens of MB) are written once at preview time; status, approval and
    review decisions live in ``<plan>.state.json`` so recording a decision never
    rewrites the whole plan.
    """
    path = store.plan_path(plan_id)
    if not os.path.exists(path):
        raise ApplyError(f"Plan {plan_id} not found")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    side = _state_path(store, plan_id)
    if os.path.exists(side):
        with open(side, encoding="utf-8") as fh:
            state = json.load(fh)
        for key in ("status", "approval", "applied_version"):
            if key in state:
                data[key] = state[key]
        decisions = state.get("decisions", {})
        for item in data.get("review_items", []):
            if item["item_id"] in decisions:
                item["decision"] = decisions[item["item_id"]]
    return Plan.model_validate(data)


def save_plan(store: StateStore, plan: Plan, full: bool = False) -> None:
    """Persist plan state. Only the sidecar is rewritten unless ``full`` is requested."""
    path = store.plan_path(plan.plan_id)
    if full or not os.path.exists(path):
        atomic_write_json(path, json.loads(plan.model_dump_json()))
    state = {
        "status": plan.status,
        "approval": plan.approval.model_dump() if plan.approval else None,
        "applied_version": plan.applied_version,
        "decisions": {i.item_id: i.decision.model_dump() for i in plan.review_items if i.decision is not None},
    }
    atomic_write_json(_state_path(store, plan.plan_id), state)


class _Collected:
    """Operations selected for execution, resolved to concrete rows."""

    def __init__(self) -> None:
        self.updates: dict[int, dict[str, Any]] = collections.defaultdict(dict)  # base row -> col -> (value, kind, action)
        self.appends: dict[int, dict[str, list[str]]] = collections.defaultdict(lambda: collections.defaultdict(list))
        self.inserts: dict[str, Operation] = {}  # identity/op key -> insert op
        self.insert_cells: dict[str, dict[str, Any]] = {}
        self.refused_fills: list[str] = []
        self.skipped_dependents: list[str] = []
        self.op_rows: dict[str, tuple[str, Any]] = {}  # op_id -> ("row", base_row) | ("insert", key)
        self.crosswalk: list[dict[str, Any]] = []


def _select_operations(plan: Plan) -> tuple[list[tuple[str, Operation]], dict[str, str]]:
    """(owner rec_id, op) pairs to execute, and decision outcome per rec_id."""
    selected: list[tuple[str, Operation]] = []
    outcome: dict[str, str] = {}
    items = plan.review_index()
    for rr in plan.records:
        if rr.operations and not rr.review_item:
            selected.extend((rr.rec_id, op) for op in rr.operations)
            outcome[rr.rec_id] = "AUTO"
    for item in plan.review_items:
        if item.decision is None:
            outcome.setdefault(item.rec_id, "UNDECIDED")
            continue
        choice = item.decision.choice
        outcome[item.rec_id] = choice
        if choice in ("REJECT", "DEFER"):
            continue
        option = item.options[item.decision.option_index]
        selected.extend((item.rec_id, op) for op in option.operations)
    # dependent records linked to a review item: their own ops run only if resolvable
    for rr in plan.records:
        if rr.operations and rr.review_item and rr.review_item in items:
            item = items[rr.review_item]
            if item.decision and item.decision.choice not in ("REJECT", "DEFER") and item.rec_id != rr.rec_id:
                selected.extend((rr.rec_id, op) for op in rr.operations)
                outcome[rr.rec_id] = f"FOLLOWS_{item.item_id}"
    return selected, outcome


def _collect(selected: list[tuple[str, Operation]], index: WarehouseIndex) -> _Collected:
    col = _Collected()
    # identity resolution: inserts and approved matches
    row_for_identity: dict[str, int] = {}
    for _owner, op in selected:
        if op.kind == "INSERT_ROW" and op.identity:
            if op.identity not in col.inserts:
                col.inserts[op.identity] = op
                col.insert_cells[op.identity] = {c.column: (c.new, c.kind) for c in op.cells if c.new is not None}
            else:
                cells = col.insert_cells[op.identity]
                for c in op.cells:
                    if c.new is not None and c.column not in cells:
                        cells[c.column] = (c.new, c.kind)
            col.op_rows[op.op_id] = ("insert", op.identity)
        elif op.kind == "UPDATE_ROW" and op.target_row is not None and op.identity:
            row_for_identity.setdefault(op.identity, op.target_row)
    base_rows = index.get_rows({op.target_row for _o, op in selected if op.target_row is not None} |
                               set(row_for_identity.values()))
    for owner, op in selected:
        if op.crosswalk:
            col.crosswalk.append(dict(op.crosswalk, owner=owner))
        if op.kind == "INSERT_ROW":
            continue
        target = op.target_row
        if target is None and op.identity:
            if op.identity in col.inserts:
                cells = col.insert_cells[op.identity]
                for c in op.cells:
                    if c.action == "APPEND":
                        current = cells.get(c.column, (None, "string"))[0]
                        merged, _added = join_tokens(current, split_tokens(c.raw or c.new))
                        cells[c.column] = (merged, "string")
                    elif c.new is not None and c.column not in cells:
                        cells[c.column] = (c.new, c.kind)
                col.op_rows[op.op_id] = ("insert", op.identity)
                continue
            target = row_for_identity.get(op.identity)
            if target is None:
                col.skipped_dependents.append(op.op_id)
                continue
        base = base_rows.get(target) or index.get_row(target) or {}
        col.op_rows[op.op_id] = ("row", target)
        for c in op.cells:
            existing = base.get(c.column)
            if c.action == "APPEND":
                col.appends[target][c.column].extend(split_tokens(c.raw or c.new))
                continue
            if c.action == "FILL" and not is_blank(existing):
                if not values_equal(c.column, existing, c.new):
                    col.refused_fills.append(f"row {target} {c.column}: not blank ({existing!r}); fill {c.new!r} refused")
                continue
            planned = col.updates[target].get(c.column)
            if planned is not None and not values_equal(c.column, planned[0], c.new):
                col.refused_fills.append(f"row {target} {c.column}: two operations disagree; first value kept")
                continue
            col.updates[target][c.column] = (c.new, c.kind, c.action)
    for target, columns in col.appends.items():
        base = base_rows.get(target) or index.get_row(target) or {}
        for column, tokens in columns.items():
            current = col.updates[target].get(column, (base.get(column),))[0]
            merged, added = join_tokens(current, tokens)
            if added:
                col.updates[target][column] = (merged, "string", "APPEND")
    return col


def apply_plan(settings: Settings, store: StateStore, plan_id: str, actor: str, authorize: bool = False,
               defer_unresolved: bool = True, force_export: bool = False, progress: ProgressFn | None = None,
               cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
    def p(phase: str, f: float, msg: str) -> None:
        if progress:
            progress(phase, max(0.0, min(1.0, f)), msg)

    with store.lock(f"apply {plan_id}"):
        plan = load_plan(store, plan_id)
        if plan.status == "APPLIED":
            raise ApplyError(f"Plan {plan_id} was already applied (version {plan.applied_version}); nothing to do")
        if plan.status == "APPROVED":
            if plan.approval is None or plan.approval.plan_hash != plan.content_hash():
                raise ApplyError("Plan changed after approval; approve it again")
        elif not authorize:
            raise ApplyError("Plan is not approved. Approve it first (`wash-sync approve`) or pass explicit authorization "
                             "(`--yes`). Ambiguous items are never auto-approved.")
        current = store.current_version()
        if current is None or current["sha256"] != plan.base_sha256:
            raise ApplyError(f"Plan was prepared for version {plan.base_version} but the current version is "
                             f"{current['version'] if current else None}. Run a new preview.")
        if plan.undecided and not defer_unresolved:
            raise ApplyError(f"{len(plan.undecided)} review item(s) are undecided; decide them or allow deferral")
        base_path = store.version_path(current)
        if file_sha256(base_path) != current["sha256"]:
            raise ApplyError("The stored base version does not match its recorded SHA-256; refusing to continue")
        index = WarehouseIndex.open_or_build(base_path, store.p("index"), current["sha256"],
                                             progress=lambda f: p("index", f, "Indexing base version"), cancelled=cancelled)
        try:
            return _apply(settings, store, plan, actor, current, base_path, index, force_export, p, cancelled)
        finally:
            index.close()


def _apply(settings: Settings, store: StateStore, plan: Plan, actor: str, current: dict[str, Any], base_path: str,
           index: WarehouseIndex, force_export: bool, p: Callable[[str, float, str], None],
           cancelled: Callable[[], bool] | None) -> dict[str, Any]:
    selected, outcome = _select_operations(plan)
    col = _collect(selected, index)
    base_headers = list(index.headers)
    used_columns = {c for row in col.updates.values() for c in row} | {c for cells in col.insert_cells.values() for c in cells}
    planned_new = {c.name: c for c in plan.new_columns}
    used_groups = {parse_period_column(c)[0] for c in used_columns if c in planned_new and parse_period_column(c)}
    new_columns = [c for c in plan.new_columns if parse_period_column(c.name) and parse_period_column(c.name)[0] in used_groups]
    for name in used_columns:
        if name not in base_headers and name not in planned_new:
            raise ApplyError(f"Operation targets unknown column {name!r}")
    final_headers = final_column_order(base_headers, new_columns)  # type: ignore[arg-type]
    n_writes = sum(len(v) for v in col.updates.values()) + len(col.inserts)
    run_id = plan.plan_id
    audit_dir = store.run_dir(run_id, "apply")
    if n_writes == 0 and not force_export:
        plan.status = "NO_CHANGES"
        save_plan(store, plan)
        _ledger_update(store, plan, outcome, None, actor)
        _update_pending(store, plan, outcome)
        store.history_append({"event": "APPLY_NO_CHANGES", "plan_id": plan.plan_id, "by": actor,
                              "deferred_items": sum(1 for v in outcome.values() if v in ("DEFER", "UNDECIDED"))})
        return {"new_version": None, "message": "No changes to apply — the warehouse already contains this data. "
                                               "No new version was created.", "plan_id": plan.plan_id,
                "refused_fills": col.refused_fills}
    styles = index.schema().column_styles
    spec = RewriteSpec(
        base_headers=base_headers, final_headers=final_headers,
        header_styles={c.name: c.header_style for c in new_columns}, widths={c.name: c.width for c in new_columns},
        sheet_name=index.sheet_name, compresslevel=settings.compression_level,
        default_row_attrs=(" " + index.meta.get("row_attrs", "").strip()).encode() if index.meta.get("row_attrs") else b"",
    )
    new_styles = {c.name: _new_column_style(c.name, styles) for c in new_columns}
    for target, cols in col.updates.items():
        spec.updates[target] = {name: CellWrite(v, kind, styles.get(name) or new_styles.get(name))
                                for name, (v, kind, _a) in cols.items()}
    for identity, op in col.inserts.items():
        cells = {name: CellWrite(v, kind, styles.get(name) or new_styles.get(name))
                 for name, (v, kind) in col.insert_cells[identity].items()}
        spec.inserts.setdefault(op.anchor_row or 1, []).append(NewRow(key=op.insert_key or identity, cells=cells))
    number = store.next_version_number()
    file_name = store.version_file_name(number)
    job_id = new_id("job")
    tmp_dir = store.p("tmp", job_id)
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, file_name)  # isolated job folder; renamed atomically when verified
    journal = store.p("jobs", f"{job_id}.json")
    atomic_write_json(journal, {"job_id": job_id, "plan_id": plan.plan_id, "status": "writing", "tmp": tmp_path,
                                "started_at": now_iso(), "pid": os.getpid()})
    try:
        p("write", 0.0, f"Writing {file_name}")
        result = rewrite_workbook(base_path, tmp_path, spec, progress=lambda f: p("write", f, f"Writing {file_name}"),
                                  cancelled=cancelled)
        atomic_write_json(journal, {"job_id": job_id, "plan_id": plan.plan_id, "status": "verifying", "tmp": tmp_path,
                                    "started_at": now_iso(), "pid": os.getpid()})
        p("verify", 0.0, "Verifying workbook integrity")
        expected_rows = index.n_rows + result.inserted_rows
        report = verify_workbook(tmp_path, expected_headers=final_headers, expected_rows=expected_rows,
                                 progress=lambda f: p("verify", f * 0.5, "Verifying workbook integrity"))
        expected_updates = {result.updated_row_numbers[b]: {k: w.value for k, w in cells.items()}
                            for b, cells in spec.updates.items() if b in result.updated_row_numbers}
        preservation = verify_against_base(
            base_path, tmp_path, base_headers, final_headers, set(result.inserted_row_numbers.values()), expected_updates,
            progress=lambda f: p("verify", 0.5 + f * 0.5, "Proving unchanged rows were preserved"))
        if not report.ok or not preservation.ok:
            failed = [f"{c.name}: {c.detail}" for c in report.checks if c.status == "FAIL"] + preservation.mismatches[:10]
            raise ApplyError("Verification failed; nothing was published:\n  " + "\n  ".join(failed))
        if cancelled is not None and cancelled():
            raise OperationCancelled("Cancelled before publishing")
        final_path = store.p("versions", file_name)
        if os.path.exists(final_path):
            raise ApplyError(f"{file_name} already exists; refusing to overwrite a published version")
        os.replace(tmp_path, final_path)
        os.chmod(final_path, 0o444)
        sha = file_sha256(final_path)
    except BaseException as exc:
        atomic_write_json(journal, {"job_id": job_id, "plan_id": plan.plan_id,
                                    "status": "cancelled" if isinstance(exc, OperationCancelled) else "failed",
                                    "error": str(exc)[:2000], "finished_at": now_iso()})
        shutil.rmtree(tmp_dir, ignore_errors=True)
        store.history_append({"event": "APPLY_FAILED" if not isinstance(exc, OperationCancelled) else "APPLY_CANCELLED",
                              "plan_id": plan.plan_id, "by": actor, "error": str(exc)[:500]})
        raise
    shutil.rmtree(tmp_dir, ignore_errors=True)
    by_class = collections.Counter()
    for rr in plan.records:
        if outcome.get(rr.rec_id) not in (None, "UNDECIDED", "REJECT", "DEFER", "KEEP_EXISTING") or (
                outcome.get(rr.rec_id) == "KEEP_EXISTING" and rr.review_item):
            by_class[rr.classification.value] += 1
    record = {
        "version": number, "file": file_name, "sha256": sha, "size": os.path.getsize(final_path),
        "created_at": now_iso(), "previous_version": current["version"], "kind": "SYNC", "plan_id": plan.plan_id,
        "sources": [{"file_name": s.file_name, "sha256": s.sha256, "source": s.source, "format": s.format,
                     "records": s.records, "periods": s.periods} for s in plan.sources],
        "schema_version": SCHEMA_VERSION, "engine_version": __version__,
        "rows": expected_rows, "columns": len(final_headers),
        "counts": {
            "added_facilities": result.inserted_rows,
            "updated_facilities": len(result.updated_row_numbers),
            "new_observations": by_class.get("NEW_OBSERVATION", 0),
            "filled_missing_values": by_class.get("FILLED_MISSING_VALUE", 0),
            "updated_measurements": by_class.get("UPDATED_MEASUREMENT", 0) + by_class.get("NEW_SOURCE_METADATA", 0),
            "duplicates_skipped": plan.summary["by_classification"].get("EXACT_DUPLICATE", 0),
            "unchanged_skipped": plan.summary["by_classification"].get("UNCHANGED", 0),
            "conflicts": len(plan.review_items),
            "deferred": sum(1 for v in outcome.values() if v in ("DEFER", "UNDECIDED")),
            "rejected": sum(1 for v in outcome.values() if v == "REJECT"),
            "invalid": plan.summary["by_classification"].get("INVALID_SOURCE_RECORD", 0),
            "refused_fills": len(col.refused_fills),
        },
        "new_columns": [c.name for c in new_columns],
        "validation": {"ok": True, "checks": [c.__dict__ for c in report.checks],
                       "preservation": {"compared_rows": preservation.compared_rows,
                                        "patched_rows_checked": preservation.patched_rows_checked,
                                        "inserted_rows": preservation.inserted_rows_skipped}},
        "approved_by": plan.approval.approved_by if plan.approval else None,
        "applied_by": actor,
    }
    store.record_version(record)
    atomic_write_json(journal, {"job_id": job_id, "plan_id": plan.plan_id, "status": "published", "version": number,
                                "finished_at": now_iso()})
    _ledger_update(store, plan, outcome, number, actor)
    store.add_crosswalk([{**c, "decided_by": actor, "decided_at": now_iso(), "plan_id": plan.plan_id,
                          "reason": c.get("reason", "")} for c in col.crosswalk
                         if outcome.get(c.get("owner")) not in (None, "UNDECIDED", "REJECT", "DEFER")])
    _update_pending(store, plan, outcome)
    _write_apply_audit(store, plan, outcome, col, result, audit_dir, number, record)
    plan.status = "APPLIED"
    plan.applied_version = number
    save_plan(store, plan)
    store.history_append({"event": "APPLY", "plan_id": plan.plan_id, "by": actor, "version": number,
                          "previous_version": current["version"], "counts": record["counts"]})
    removed = store.apply_retention(settings.backup_retention)
    if removed:
        store.history_append({"event": "RETENTION", "removed": removed, "by": actor})
    p("done", 1.0, f"Published {file_name}")
    return {"new_version": number, "file": file_name, "sha256": sha, "counts": record["counts"],
            "rows": expected_rows, "columns": len(final_headers), "audit_dir": audit_dir,
            "refused_fills": col.refused_fills[:50], "skipped_dependents": col.skipped_dependents}


def _new_column_style(name: str, styles: dict[str, str | None]) -> str | None:
    parsed = parse_period_column(name)
    if not parsed:
        return None
    metric = parsed[1]
    candidates = [c for c in styles if c.endswith("_" + metric) and parse_period_column(c)]
    return styles.get(candidates[-1]) if candidates else None


def _ledger_update(store: StateStore, plan: Plan, outcome: dict[str, str], version: int | None, actor: str) -> None:
    ledger = store.ledger()
    stamp = now_iso()
    fp_path = os.path.join(plan.audit_dir or store.run_dir(plan.plan_id, "preview"), "fingerprints.jsonl")
    status_map = {"AUTO": "APPLIED", "ACCEPT": "APPLIED", "ACCEPT_NEW_VALUES": "APPLIED", "APPROVE_MATCH": "APPLIED",
                  "KEEP_SEPARATE": "APPLIED", "KEEP_EXISTING": "KEPT_EXISTING", "REJECT": "REJECTED",
                  "DEFER": "DEFERRED", "UNDECIDED": "DEFERRED"}
    if not os.path.exists(fp_path):
        return
    with open(fp_path, encoding="utf-8") as fh:
        for line in fh:
            e = json.loads(line)
            cls = e["c"]
            decided = outcome.get(e["r"])
            if decided is not None:
                status = status_map.get(decided, "APPLIED" if decided.startswith("FOLLOWS_") else decided)
            elif cls == ChangeClass.UNCHANGED.value:
                status = "SYNCED"
            elif cls == ChangeClass.EXACT_DUPLICATE.value:
                continue
            elif cls == ChangeClass.INVALID_SOURCE_RECORD.value:
                status = "INVALID"
            else:
                status = "SEEN"
            if version is None and status == "APPLIED":
                status = "SYNCED"
            prev = ledger.get(e["k"], {})
            ledger[e["k"]] = {"k": e["k"], "h": e["h"], "s": status, "src": e.get("s"),
                              "v": version if status == "APPLIED" else prev.get("v"),
                              "p": plan.plan_id, "t": stamp, "by": actor,
                              "prev_h": prev.get("h") if prev.get("h") != e["h"] else prev.get("prev_h")}
    store.save_ledger(ledger)


def _update_pending(store: StateStore, plan: Plan, outcome: dict[str, str]) -> None:
    by_rec = {r.rec_id: r for r in plan.records}
    keys_in_plan = {r.observation_key for r in plan.records}
    pending = [p for p in store.pending() if p.get("observation_key") not in keys_in_plan]
    for item in plan.review_items:
        choice = item.decision.choice if item.decision else None
        if choice in (None, "DEFER"):
            rr = by_rec.get(item.rec_id)
            pending.append({"item_id": item.item_id, "plan_id": plan.plan_id, "classification": item.classification.value,
                            "title": item.title, "reason": item.reason, "observation_key": rr.observation_key if rr else None,
                            "source": rr.source if rr else None, "source_file": rr.source_file if rr else None,
                            "source_row": rr.source_row if rr else None, "deferred_at": now_iso(),
                            "status": "DEFERRED" if choice == "DEFER" else "UNDECIDED"})
    store.save_pending(pending)


def _write_apply_audit(store: StateStore, plan: Plan, outcome: dict[str, str], col: _Collected, result: Any,
                       audit_dir: str, version: int, record: dict[str, Any]) -> None:
    audit = AuditWriter(audit_dir, plan.plan_id, "apply")
    ops_by_owner: dict[str, list[Operation]] = collections.defaultdict(list)
    items = plan.review_index()
    for rr in plan.records:
        ops_by_owner[rr.rec_id].extend(rr.operations)
    for item in plan.review_items:
        if item.decision and item.decision.choice not in ("REJECT", "DEFER"):
            ops_by_owner[item.rec_id].extend(item.options[item.decision.option_index].operations)

    def destination(op: Operation) -> int | None:
        where = col.op_rows.get(op.op_id)
        if where is None:
            return None
        if where[0] == "row":
            return result.updated_row_numbers.get(where[1])
        ins = col.inserts.get(where[1])
        return result.inserted_row_numbers.get(ins.insert_key or where[1]) if ins else None

    for rr in plan.records:
        decided = outcome.get(rr.rec_id)
        ops = ops_by_owner.get(rr.rec_id, [])
        executed = [op for op in ops if op.op_id in col.op_rows]
        dest = next((destination(op) for op in executed if destination(op)), None)
        if executed:
            status, action = "APPLIED", "; ".join(f"{op.kind} {len(op.cells)} cells" for op in executed)
        elif decided in ("REJECT",):
            status, action = "REJECTED", "none"
        elif decided in ("DEFER", "UNDECIDED"):
            status, action = "DEFERRED", "none (awaiting review)"
        else:
            status, action = "SKIPPED", "none"
        item = items.get(rr.review_item) if rr.review_item else None
        audit.record(rr, action, status, dest, decision=item.decision.choice if item and item.decision else decided,
                     reason=item.decision.reason if item and item.decision else None)
        if item is not None and item.rec_id == rr.rec_id:
            audit.conflict(item, rr, status)
        for op in executed:
            dest = destination(op)
            for c in op.cells:
                audit.write("field_change_history", {
                    "source": rr.source, "input_file": rr.source_file, "input_row": rr.source_row,
                    "source_record_id": rr.source_record_id, "observation_key": rr.observation_key,
                    "target_candidate_id": op.target_candidate_id, "destination_row": dest, "column": c.column,
                    "action": c.action, "original_value": c.old, "new_value": c.new, "new_value_source_text": c.raw,
                    "policy": c.policy, "category": c.category, "reason": rr.proposed_action, "status": status,
                })
            if op.kind == "INSERT_ROW":
                cells = {c.column: c.new for c in op.cells}
                audit.write("new_facilities", {
                    "source": rr.source, "input_file": rr.source_file, "input_row": rr.source_row,
                    "source_record_id": rr.source_record_id, "new_candidate_id": cells.get("candidate_id"),
                    **{k: cells.get(k) for k in ("division", "district", "upazila", "union_name", "village_name",
                                                 "latitude", "longitude")},
                    "anchor_row": op.anchor_row, "placement": op.anchor_explanation, "destination_row": dest,
                    "status": status,
                })
    summary = {"plan_id": plan.plan_id, "version": version, "file": record["file"], "sha256": record["sha256"],
               "previous_version": record["previous_version"], "counts": record["counts"],
               "new_columns": record["new_columns"], "rows": record["rows"], "columns": record["columns"],
               "validation": record["validation"], "refused_fills": col.refused_fills[:200],
               "skipped_dependent_operations": col.skipped_dependents, "output_version": version,
               "by_classification": plan.summary.get("by_classification", {})}
    audit.close(summary, plan)
    # carry the full (unchanged + duplicate) record list from the preview audit into the apply folder
    preview_csv = os.path.join(store.run_dir(plan.plan_id, "preview"), "record_changes.csv")
    if os.path.exists(preview_csv):
        shutil.copyfile(preview_csv, os.path.join(audit_dir, "record_changes_preview_all_records.csv"))
