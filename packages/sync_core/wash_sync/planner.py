"""Dry-run planning: compare source files with the current warehouse version.

Nothing is written to any warehouse here. The output is a :class:`Plan` (saved in
the private state directory) plus a complete preview audit.
"""

from __future__ import annotations

import collections
import dataclasses
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import SCHEMA_VERSION, __version__
from .adapters.base import AdapterContext, Detection, detect_source, parse_survey_period, read_records
from .adapters.tabular import estimate_rows
from .admin import LEVELS, AdminResolver, AliasTable
from .audit import AuditWriter
from .changes import CellTyper, Diff, dependent_cells, diff_record, is_blank
from .config import Settings
from .index import WarehouseIndex, file_sha256
from .matching import Matcher
from .plan import (
    CellChange,
    DecisionRecord,
    MatchInfo,
    NewColumnModel,
    Operation,
    Plan,
    RecordResult,
    ReviewItem,
    ReviewOption,
    SourceFileInfo,
)
from .records import ChangeClass, Policy, SourceRecord, compute_fingerprint
from .schema import NewColumn, Period
from .state import StateStore, new_id, now_iso
from .text import join_tokens, norm_key
from .validation import apply_policy

SOURCE_FLAG_COLUMN = {"AQUAWATCH": "aqua_quality_flags", "WPMS": "wpms_quality_flags", "BORELOG": "borelog_quality_flags"}
ProgressFn = Callable[[str, float, str], None]


class PlanningCancelled(RuntimeError):
    pass


@dataclass
class PreviewInput:
    path: str
    source: str | None = None
    period: str | None = None
    display_name: str | None = None


def build_resolver(index: WarehouseIndex, aliases: AliasTable) -> AdminResolver:
    display: dict[str, dict[str, str]] = {}
    for level, mapping in index.display_names().items():
        best: dict[str, tuple[str, int]] = {}
        for key, (name, support) in mapping.items():
            canonical = aliases.by_level.get(level, {}).get(key, key)
            if canonical not in best or support > best[canonical][1]:
                best[canonical] = (name, support)
        display[level] = {k: v[0] for k, v in best.items()}
    return AdminResolver(aliases=aliases, display=display, bangla=index.bangla_aliases())


def identity_key(rec: SourceRecord) -> str:
    if rec.source == "AQUAWATCH" and rec.identity_hints.get("historical"):
        return rec.observation_key
    return f"{rec.source}|{rec.source_record_id}"


class Planner:
    def __init__(self, settings: Settings, store: StateStore, progress: ProgressFn | None = None,
                 cancelled: Callable[[], bool] | None = None) -> None:
        self.settings = settings
        self.store = store
        self._progress = progress
        self._cancelled = cancelled

    def _p(self, phase: str, fraction: float, message: str) -> None:
        if self._progress:
            self._progress(phase, max(0.0, min(1.0, fraction)), message)

    def _check_cancel(self) -> None:
        if self._cancelled is not None and self._cancelled():
            raise PlanningCancelled("Preview cancelled by user")

    # ================================================================== main
    def run(self, inputs: list[PreviewInput], actor: str = "local-user") -> Plan:
        started = time.time()
        current = self.store.current_version()
        if current is None:
            raise RuntimeError("No warehouse registered yet. Run `wash-sync init --warehouse <file>` first.")
        base_path = self.store.version_path(current)
        self._p("index", 0.0, f"Loading index for warehouse v{current['version']:03d}")
        index = WarehouseIndex.open_or_build(
            base_path, self.store.p("index"), current["sha256"],
            progress=lambda f: self._p("index", f, "Indexing warehouse (one-time per version)"),
            cancelled=self._cancelled)
        try:
            return self._run(inputs, actor, current, index, started)
        finally:
            index.close()

    def _run(self, inputs: list[PreviewInput], actor: str, current: dict[str, Any], index: WarehouseIndex,
             started: float) -> Plan:
        s = self.settings
        self.index = index
        self.schema = index.schema()
        self.aliases = AliasTable.from_config(s.admin_aliases)
        self.resolver = build_resolver(index, self.aliases)
        self.matcher = Matcher(index, s, self.resolver, self.store.crosswalk())
        self.ledger = self.store.ledger()
        plan_id = new_id("plan")
        self.plan = Plan(
            plan_id=plan_id, created_at=now_iso(), engine_version=__version__, schema_version=SCHEMA_VERSION,
            base_version=current["version"], base_file=current["file"], base_sha256=current["sha256"],
            base_rows=index.n_rows, base_columns=len(index.headers),
            settings=s.model_dump(exclude={"state_dir", "private_storage", "source_dirs", "export_dir"}),
        )
        audit_dir = self.store.run_dir(plan_id, "preview")
        self.plan.audit_dir = audit_dir
        self.audit = AuditWriter(audit_dir, plan_id, "preview")
        self.counts: collections.Counter[str] = collections.Counter()
        self.by_source: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
        self.new_columns: list[NewColumn] = []
        self.new_types: dict[str, str] = {}
        self.typer = CellTyper(self.schema, self.new_types)
        self.run_targets: dict[str, dict[str, Any]] = {}
        self.items_by_key: dict[str, ReviewItem] = {}
        self._op_seq = 0
        self._item_seq = 0
        self._seen_keys: dict[str, tuple[str, str]] = {}
        fp_path = os.path.join(audit_dir, "fingerprints.jsonl")
        detections = self._scan(inputs)
        total = sum(d["records"] for d in detections) or 1
        done = 0
        with open(fp_path, "w", encoding="utf-8") as fp_out:
            for file_idx, (inp, scan) in enumerate(zip(inputs, detections)):
                info: SourceFileInfo = scan["info"]
                det: Detection = scan["detection"]
                if det.source is None or det.missing_required:
                    continue
                ctx = self._ctx(inp, det)
                completeness: collections.Counter[str] = collections.Counter()
                for rec in read_records(inp.path, ctx, det):
                    done += 1
                    if done % 250 == 0:
                        self._check_cancel()
                        self._p("compare", done / total, f"Compared {done:,} of {total:,} source records")
                    apply_policy(rec, s.validation)
                    rec.content_hash = compute_fingerprint(rec)
                    for issue in rec.issues:
                        if issue.code.startswith("INCOMPLETE_"):
                            completeness[issue.code] += 1
                    rr = self._process(rec, f"{file_idx}:{rec.source_row}", scan["conflicting_keys"])
                    self.counts[rr.classification.value] += 1
                    self.by_source[rec.source][rr.classification.value] += 1
                    fp_out.write(json.dumps({"k": rr.observation_key, "h": rr.content_hash, "c": rr.classification.value,
                                             "r": rr.rec_id, "s": rr.source}, ensure_ascii=False) + "\n")
                    self.audit.record(rr, "NOT_EXECUTED (dry run)", "PROPOSED")
                    if rr.classification != ChangeClass.UNCHANGED or self.counts["UNCHANGED"] <= 200:
                        self.plan.records.append(rr)
                info.completeness = dict(completeness)
        self.plan.sources = [d["info"] for d in detections]
        self.plan.new_columns = [NewColumnModel(**dataclasses.asdict(c)) for c in self.new_columns]
        for item in self.plan.review_items:
            rr = self._rec_by_id(item.rec_id)
            if rr is not None:
                self.audit.conflict(item, rr, "DECIDED" if item.decision else "AWAITING_REVIEW")
        self.plan.summary = self._summary()
        if not any(r.operations for r in self.plan.records) and not self.plan.review_items:
            self.plan.status = "NO_CHANGES"
        self.plan.elapsed_seconds = round(time.time() - started, 2)
        self.audit.close(self.plan.summary, self.plan)
        from .apply import save_plan  # noqa: PLC0415

        save_plan(self.store, self.plan, full=True)
        self.store.history_append({"event": "PREVIEW", "plan_id": plan_id, "by": actor, "base_version": current["version"],
                                   "files": [i.file_name for i in self.plan.sources], "summary": self.plan.summary["by_classification"]})
        self._p("done", 1.0, "Preview complete")
        return self.plan

    # ================================================================== scan
    def _ctx(self, inp: PreviewInput, det: Detection) -> AdapterContext:
        forced = None
        if inp.period:
            forced, problem = parse_survey_period(inp.period.replace("_", " ").replace("annual", ""))
            if problem:
                raise ValueError(f"Invalid period override {inp.period!r}: {problem}")
        return AdapterContext(admin=self.resolver, file_name=inp.display_name or os.path.basename(inp.path),
                              file_path=inp.path, strict=self.settings.validation.mode == "strict", forced_period=forced)

    def _scan(self, inputs: list[PreviewInput]) -> list[dict[str, Any]]:
        out = []
        hashes: dict[str, set[str]] = collections.defaultdict(set)
        for n, inp in enumerate(inputs):
            self._check_cancel()
            self._p("scan", n / max(1, len(inputs)), f"Reading {inp.display_name or os.path.basename(inp.path)}")
            det = detect_source(inp.path, inp.source)
            info = SourceFileInfo(
                file_name=inp.display_name or os.path.basename(inp.path), sha256=file_sha256(inp.path),
                size=os.path.getsize(inp.path), source=det.source, format=det.format, sheet=det.sheet,
                confidence=det.confidence, matched_columns=det.matched, missing_optional=det.missing_optional,
                ignored_columns=det.ignored_columns, personal_data_columns_ignored=det.personal_data_columns,
            )
            scan: dict[str, Any] = {"info": info, "detection": det, "records": 0, "conflicting_keys": set()}
            if det.source is None or det.missing_required:
                info.completeness = {"error": det.reason or f"missing required columns {det.missing_required}"}
                out.append(scan)
                continue
            periods: collections.Counter[str] = collections.Counter()
            ctx = self._ctx(inp, det)
            try:
                info_rows = estimate_rows(inp.path, det.sheet)
            except Exception:  # noqa: BLE001
                info_rows = None
            for i, rec in enumerate(read_records(inp.path, ctx, det)):
                if i % 500 == 0:
                    self._check_cancel()
                    if info_rows:
                        self._p("scan", (n + min(1.0, i / info_rows)) / max(1, len(inputs)),
                                f"Scanning {info.file_name}: {i:,} records")
                scan["records"] += 1
                hashes[rec.observation_key].add(compute_fingerprint(rec))
                if rec.period is not None and not rec.has_errors:
                    periods[rec.period.key] += 1
                    self._ensure_period(rec.period)
            info.records = scan["records"]
            info.periods = sorted(periods)
            out.append(scan)
        # A key is conflicting when it carries different content anywhere in the run
        # (within one file or across files); all of its variants then go to review.
        conflicting = {k for k, v in hashes.items() if len(v) > 1}
        for scan in out:
            scan["conflicting_keys"] = conflicting
        return out

    def _ensure_period(self, period: Period) -> None:
        if self.schema.has_period(period):
            return
        if any(c.name == period.column("arsenic_raw") for c in self.new_columns):
            return
        planned = self.schema.plan_period_columns(period, self.new_columns)
        self.new_columns.extend(planned)
        for col in planned:
            self.new_types[col.name] = col.value_type

    # ================================================================== records
    def _rec_by_id(self, rec_id: str) -> RecordResult | None:
        for rr in reversed(self.plan.records):
            if rr.rec_id == rec_id:
                return rr
        return None

    def _base_result(self, rec: SourceRecord, rec_id: str) -> RecordResult:
        return RecordResult(
            rec_id=rec_id, source=rec.source, format=rec.format, source_file=rec.source_file,
            source_sheet=rec.source_sheet, source_row=rec.source_row, source_record_id=rec.source_record_id,
            observation_key=rec.observation_key, content_hash=rec.content_hash,
            period=rec.period.key if rec.period else None, classification=ChangeClass.UNCHANGED,
            issues=[i.to_dict() for i in rec.issues], flags=list(rec.flags), preview=rec.preview,
        )

    def _process(self, rec: SourceRecord, rec_id: str, conflicting: set[str]) -> RecordResult:
        rr = self._base_result(rec, rec_id)
        if rec.has_errors:
            rr.classification = ChangeClass.INVALID_SOURCE_RECORD
            rr.proposed_action = "Reject: " + "; ".join(i.message for i in rec.issues if i.severity == "ERROR")
            return rr
        seen = self._seen_keys
        prior = seen.get(rec.observation_key)
        if prior is not None and prior[0] == rec.content_hash:
            rr.classification = ChangeClass.EXACT_DUPLICATE
            rr.duplicate_of = prior[1]
            rr.proposed_action = f"Skip: identical to record {prior[1]} earlier in this run"
            return rr
        in_conflict = rec.observation_key in conflicting
        if prior is None:
            seen[rec.observation_key] = (rec.content_hash, rec_id)
        identity = identity_key(rec)
        had_target = identity in self.run_targets
        target = None if in_conflict else self.run_targets.get(identity)
        if target is not None:
            self._dependent(rec, rr, identity, target)
        else:
            match = self.matcher.match(rec)
            rr.match = match
            if match.category in ("CONFLICTING_IDENTIFIERS", "POSSIBLE_DUPLICATE"):
                self._identity_review(rec, rr, identity, match)
            elif match.candidate_rows:
                self._existing_row(rec, rr, identity, match)
            else:
                self._new_facility(rec, rr, identity, match)
        if in_conflict and not had_target:
            self.run_targets.pop(identity, None)  # variants are alternatives; none may anchor other records
        if in_conflict and rr.classification not in (ChangeClass.UNCHANGED, ChangeClass.INVALID_SOURCE_RECORD):
            self._measurement_conflict(rec, rr)
        elif in_conflict and rr.classification == ChangeClass.UNCHANGED:
            rr.notes.append("Another record in this run has different values for the same observation; this one "
                            "matches the warehouse")
        self._apply_review_issues(rec, rr)
        self._remembered_decision(rr)
        return rr

    # ------------------------------------------------------------------ helpers
    def _op(self, kind: str, **kw: Any) -> Operation:
        self._op_seq += 1
        return Operation(op_id=f"op{self._op_seq:06d}", kind=kind, **kw)

    def _new_item(self, rr: RecordResult, cls: ChangeClass, title: str, reason: str,
                  options: list[ReviewOption], recommended: int | None) -> ReviewItem:
        self._item_seq += 1
        item = ReviewItem(item_id=f"R{self._item_seq:05d}", rec_id=rr.rec_id, classification=cls, title=title,
                          reason=reason, options=options, recommended=recommended)
        self.plan.review_items.append(item)
        rr.review_item = item.item_id
        rr.classification = cls
        return item

    def _flag_cells(self, rec: SourceRecord, row: dict[str, Any], tokens: list[str]) -> list[CellChange]:
        out: list[CellChange] = []
        if tokens:
            new, added = join_tokens(row.get("record_sources"), tokens)
            if added:
                out.append(CellChange(column="record_sources", old=row.get("record_sources"), new=new, kind="string",
                                      action="APPEND", raw=";".join(added), policy="flags"))
        if rec.flags:
            for column in ("quality_flags", SOURCE_FLAG_COLUMN.get(rec.source)):
                if column and self._column_exists(column):
                    new, added = join_tokens(row.get(column), rec.flags)
                    if added:
                        out.append(CellChange(column=column, old=row.get(column), new=new, kind="string",
                                              action="APPEND", raw=";".join(added), policy="flags"))
        return out

    def _column_exists(self, column: str) -> bool:
        return self.schema.has(column) or any(c.name == column for c in self.new_columns)

    def _source_tokens(self, rec: SourceRecord, row: dict[str, Any], level: str) -> list[str]:
        if rec.source == "AQUAWATCH" and rec.period is not None:
            return [rec.period.source_token]
        if level.startswith("L2") or level == "L3_GEOGRAPHIC":
            return [rec.source]
        return []

    def _update_cells(self, rec: SourceRecord, row: dict[str, Any], diff: Diff, include_sets: bool,
                      level: str) -> list[CellChange]:
        cells: list[CellChange] = []
        cells.extend(diff.identity_fills)
        cells.extend(diff.fills)
        cells.extend(diff.appended_layers)
        if include_sets:
            cells.extend(diff.sets)
        if cells:
            cells.extend(diff.piggyback)
            written = {c.column for c in cells}
            cells.extend(dependent_cells(rec, written, row, self.typer, diff.groups_new))
            cells.extend(self._flag_cells(rec, row, self._source_tokens(rec, row, level)))
            if level.startswith("L2") and rec.source == "BORELOG" and is_blank(row.get("borelog_match_status")):
                status = ("MERGED_GPANI_UNIQUE_TW_ID_ADMIN_GPS_TYPE_DEPTH" if level == "L2_VERIFIED_CROSS_SOURCE"
                          else "MERGED_REVIEWED_MATCH")
                cells.append(CellChange(column="borelog_match_status", new=status, kind="string", action="FILL",
                                        policy="identity"))
        return self._typed_unique(cells)

    def _typed_unique(self, cells: list[CellChange]) -> list[CellChange]:
        out: dict[str, CellChange] = {}
        for cell in cells:
            if cell.column not in out:
                out[cell.column] = cell
        return list(out.values())

    def _insert_cells(self, rec: SourceRecord, extra_status: dict[str, Any] | None = None) -> list[CellChange]:
        values: dict[str, tuple[Any, str]] = {}
        for column, value in rec.core.items():
            if value is None or not self._column_exists(column):
                continue
            kind = "number" if isinstance(value, (int, float)) and not isinstance(value, bool) else "string"
            values[column] = (value, kind)
        for column, fv in rec.fields.items():
            if fv.value is None or column in values or fv.policy == Policy.FLAGS or not self._column_exists(column):
                continue
            kind, value = self.typer(column, fv.value, fv.value_type)
            values[column] = (value, kind)
        for column, value in (extra_status or {}).items():
            if value is not None and self._column_exists(column):
                values[column] = (value, "string" if isinstance(value, str) else "number")
        if rec.flags:
            joined = ";".join(dict.fromkeys(rec.flags))
            values["quality_flags"] = (joined, "string")
            flag_col = SOURCE_FLAG_COLUMN.get(rec.source)
            if flag_col and self._column_exists(flag_col):
                values[flag_col] = (joined, "string")
        cells = []
        for column, (value, kind) in values.items():
            fv = rec.fields.get(column)
            cells.append(CellChange(column=column, new=value, kind=kind, action="INSERT",
                                    raw=fv.raw if fv else None, policy=fv.policy.value if fv else "core",
                                    category=fv.category.value if fv else None))
        return cells

    def _insert_op(self, rec: SourceRecord, identity: str, extra_status: dict[str, Any] | None = None,
                   crosswalk: dict[str, Any] | None = None) -> Operation:
        admin = {lvl: rec.core.get(lvl) for lvl in LEVELS}
        anchor = self.index.insertion_anchor(admin, self.aliases)
        insert_key = "|".join(norm_key(admin.get(lvl)) for lvl in LEVELS) + "|" + str(rec.core.get("candidate_id"))
        return self._op("INSERT_ROW", anchor_row=anchor.after_row, anchor_explanation=anchor.explanation,
                        insert_key=insert_key, identity=identity, target_candidate_id=rec.core.get("candidate_id"),
                        cells=self._insert_cells(rec, extra_status), crosswalk=crosswalk)

    # ------------------------------------------------------------------ cases
    def _existing_row(self, rec: SourceRecord, rr: RecordResult, identity: str, match: MatchInfo) -> None:
        row_num = match.candidate_rows[0]
        row = self.matcher.row(row_num)
        level = match.level
        diff = diff_record(rec, row, self.typer, allow_identity_fill=level.startswith("L2"))
        attribute_notes = [f"Existing {column} = {old!r} differs from source {new!r}; existing value kept (facility attribute)"
                           for column, old, new in diff.fill_only_conflicts]
        auto_cells = self._update_cells(rec, row, diff, include_sets=False, level=level)
        crosswalk = None
        if level == "L2_VERIFIED_CROSS_SOURCE":
            crosswalk = {"source": rec.source, "source_record_id": rec.source_record_id,
                         "candidate_id": row.get("candidate_id"), "link_type": "VERIFIED_AUTO",
                         "evidence": match.evidence_text()[:500]}
        target_cid = str(row.get("candidate_id"))
        if diff.sets:
            rr.notes.extend(attribute_notes)
            cls = ChangeClass.UPDATED_MEASUREMENT if diff.measurement_sets else ChangeClass.NEW_SOURCE_METADATA
            accept = self._update_cells(rec, row, diff, include_sets=True, level=level)
            changed = ", ".join(f"{c.column}: {c.old!r} → {c.new!r}" for c in diff.sets[:6])
            options = [ReviewOption(decision="ACCEPT_NEW_VALUES", label="Accept the new values",
                                    description=f"Overwrite {len(diff.sets)} existing value(s): {changed}",
                                    operations=[self._op("UPDATE_ROW", target_row=row_num, target_candidate_id=target_cid,
                                                         cells=accept, crosswalk=crosswalk)])]
            if auto_cells:
                options.append(ReviewOption(decision="KEEP_EXISTING", label="Keep existing values, fill blanks only",
                                            description="Keep every existing value; only fill blank fields",
                                            operations=[self._op("UPDATE_ROW", target_row=row_num,
                                                                 target_candidate_id=target_cid, cells=auto_cells,
                                                                 crosswalk=crosswalk)]))
            else:
                options.append(ReviewOption(decision="KEEP_EXISTING", label="Keep existing values",
                                            description="Keep the warehouse unchanged for this record"))
            options += [ReviewOption(decision="REJECT", label="Reject this source record"),
                        ReviewOption(decision="DEFER", label="Defer for later review")]
            self._new_item(rr, cls, f"{rec.source} {rec.source_record_id}: {len(diff.sets)} value(s) differ from row {row_num}",
                           f"Changed existing values: {changed}", options,
                           recommended=0 if cls == ChangeClass.NEW_SOURCE_METADATA else None)
            rr.proposed_action = f"Review {len(diff.sets)} changed value(s) on row {row_num}"
            return
        if not auto_cells:
            rr.classification = ChangeClass.UNCHANGED
            rr.proposed_action = f"Skip: already present on row {row_num}"
            return
        rr.notes.extend(attribute_notes)
        if diff.groups_new or diff.appended_layers or diff.identity_fills:
            rr.classification = ChangeClass.NEW_OBSERVATION
            what = (f"{rec.period.label} observation" if rec.period else
                    ("new geological layers" if diff.appended_layers else f"{rec.source} data"))
            rr.proposed_action = f"Add {what} to row {row_num} ({target_cid})"
        else:
            rr.classification = ChangeClass.FILLED_MISSING_VALUE
            rr.proposed_action = f"Fill {len(diff.fills)} blank field(s) on row {row_num}"
        op = self._op("UPDATE_ROW", target_row=row_num, target_candidate_id=target_cid, cells=auto_cells, crosswalk=crosswalk)
        if level == "L2_VERIFIED_CROSS_SOURCE" and not self.settings.matching.auto_apply_verified_cross_source:
            options = [ReviewOption(decision="APPROVE_MATCH", label=f"Approve verified match to row {row_num}",
                                    candidate_row=row_num, operations=[op]),
                       ReviewOption(decision="KEEP_SEPARATE", label="Keep as a separate facility",
                                    operations=[self._insert_op(rec, identity)]),
                       ReviewOption(decision="REJECT", label="Reject"), ReviewOption(decision="DEFER", label="Defer")]
            self._new_item(rr, ChangeClass.REQUIRES_REVIEW, f"Verified cross-source match for {rec.source} {rec.source_record_id}",
                           match.note or "", options, recommended=0)
            self.run_targets[identity] = {"type": "review", "item": rr.review_item, "virtual": dict(row)}
            return
        rr.operations = [op]

    def _new_facility(self, rec: SourceRecord, rr: RecordResult, identity: str, match: MatchInfo) -> None:
        if not rec.core.get("candidate_id"):
            rr.classification = ChangeClass.INVALID_SOURCE_RECORD
            rr.proposed_action = "Reject: no identity available to create a facility row"
            return
        extra = self._bore_status(rec, "NEW_NO_CODE_MATCH" if rec.identity_hints.get("tw_code") else "NEW_MISSING_TW_ID") \
            if rec.source == "BORELOG" else None
        op = self._insert_op(rec, identity, extra)
        rr.classification = ChangeClass.NEW_FACILITY
        rr.operations = [op]
        rr.proposed_action = f"Insert new facility {rec.core.get('candidate_id')} after row {op.anchor_row} ({op.anchor_explanation})"
        self.run_targets[identity] = {"type": "insert", "virtual": {c.column: c.new for c in op.cells}}

    def _bore_status(self, rec: SourceRecord, status: str) -> dict[str, Any]:
        return {"borelog_match_status": status, "match_evidence": status}

    def _identity_review(self, rec: SourceRecord, rr: RecordResult, identity: str, match: MatchInfo) -> None:
        options: list[ReviewOption] = []
        for cand in match.candidates[:5]:
            row_num = cand["row"]
            row = self.matcher.row(row_num)
            diff = diff_record(rec, row, self.typer, allow_identity_fill=True)
            cells = self._update_cells(rec, row, diff, include_sets=False, level="L3_GEOGRAPHIC")
            if rec.source == "BORELOG" and is_blank(row.get("borelog_match_status")):
                cells.append(CellChange(column="borelog_match_status", new="MERGED_REVIEWED_MATCH", kind="string",
                                        action="FILL", policy="identity"))
            kept = f"; {len(diff.sets)} differing value(s) keep their existing values" if diff.sets else ""
            dist = f" ({cand['distance_m']} m away)" if "distance_m" in cand else ""
            options.append(ReviewOption(
                decision="APPROVE_MATCH", label=f"Same facility as row {row_num} — {cand.get('candidate_id')}{dist}",
                description=f"Write {len(cells)} cell(s) into the existing row{kept}", candidate_row=row_num,
                operations=[self._op("UPDATE_ROW", target_row=row_num, target_candidate_id=str(row.get("candidate_id")),
                                     cells=cells, crosswalk={"source": rec.source, "source_record_id": rec.source_record_id,
                                                             "candidate_id": row.get("candidate_id"),
                                                             "link_type": "APPROVED_MATCH",
                                                             "evidence": match.evidence_text()[:500]})]))
        separate_ops: list[Operation] = []
        if rec.core.get("candidate_id"):
            status = "BORELOG_POSSIBLE_EXISTING_WELL_REVIEW" if rec.source == "BORELOG" else None
            extra = self._bore_status(rec, "NEW_REVIEWED_KEPT_SEPARATE") if rec.source == "BORELOG" else {}
            if status:
                extra["match_status"] = status
            separate_ops = [self._insert_op(rec, identity, extra, crosswalk={
                "source": rec.source, "source_record_id": rec.source_record_id,
                "candidate_id": ";".join(match.candidate_ids[:5]), "link_type": "KEEP_SEPARATE",
                "evidence": match.evidence_text()[:500]})]
        options.append(ReviewOption(decision="KEEP_SEPARATE", label="Keep as a separate facility (new row)",
                                    description="Create a new geographically grouped facility row", operations=separate_ops))
        options += [ReviewOption(decision="REJECT", label="Reject this source record"),
                    ReviewOption(decision="DEFER", label="Defer for later review")]
        if match.category == "POSSIBLE_DUPLICATE":
            cls, recommended = ChangeClass.POTENTIAL_DUPLICATE, len(options) - 3
            title = f"{rec.source} {rec.source_record_id}: {len(match.candidates)} nearby compatible facilit(ies)"
        else:
            cls = ChangeClass.CONFLICTING_IDENTITY
            keep_sep = {"GEOCODE_REUSED_OR_GPS_CHANGED", "UNRESOLVED_DUPLICATE_TW_ID"}
            recommended = len(options) - 3 if (set(match.conflicts) & keep_sep or any(
                c.startswith(("UNRESOLVED_CODE", "EXACT_GO_CODE")) for c in match.conflicts)) else len(options) - 1
            title = f"{rec.source} {rec.source_record_id}: conflicting identifiers ({', '.join(match.conflicts)})"
        self._new_item(rr, cls, title, match.note or "", options, recommended)
        rr.proposed_action = "Hold for review: " + (match.note or cls.value)
        self.run_targets[identity] = {"type": "review", "item": rr.review_item,
                                      "virtual": {c.column: c.new for o in separate_ops for c in o.cells}}

    def _dependent(self, rec: SourceRecord, rr: RecordResult, identity: str, target: dict[str, Any]) -> None:
        virtual = target["virtual"]
        diff = diff_record(rec, virtual, self.typer, allow_identity_fill=False)
        cells = self._update_cells(rec, virtual, diff, include_sets=False, level="WITHIN_RUN")
        rr.match = MatchInfo(level="WITHIN_RUN", confidence="EXACT", category="SAME_FACILITY",
                             note="Facility is created by another record in this run (same source identity)")
        for column, old, new in diff.fill_only_conflicts:
            rr.notes.append(f"{column}: {old!r} (earlier record in this run) kept; this record reports {new!r}")
        if diff.sets:
            rr.notes.append(f"{len(diff.sets)} value(s) conflict with the earlier record in this run and were not applied")
        if not cells:
            rr.classification = ChangeClass.UNCHANGED
            rr.proposed_action = "Skip: nothing new beyond the record that creates this facility"
            return
        for cell in cells:
            virtual[cell.column] = cell.new
        rr.classification = ChangeClass.NEW_OBSERVATION
        rr.operations = [self._op("UPDATE_ROW", identity=identity, cells=cells)]
        what = f"{rec.period.label} observation" if rec.period else "data"
        if target["type"] == "review":
            rr.review_item = target["item"]
            rr.notes.append(f"Applied only if review item {target['item']} results in a facility row")
        rr.proposed_action = f"Add {what} to the facility created in this run ({identity})"

    def _measurement_conflict(self, rec: SourceRecord, rr: RecordResult) -> None:
        key = rec.observation_key
        item = self.items_by_key.get(key)
        ops = list(rr.operations)
        if rr.review_item and not ops:
            own = next((i for i in self.plan.review_items if i.item_id == rr.review_item), None)
            if own is not None:
                accept = next((o for o in own.options if o.decision in ("ACCEPT_NEW_VALUES", "KEEP_SEPARATE", "ACCEPT")), None)
                ops = list(accept.operations) if accept else []
                self.plan.review_items.remove(own)
        label = f"Use values from {rec.source_file} row {rec.source_row}"
        option = ReviewOption(decision="ACCEPT", label=label, description=", ".join(
            f"{c.column}={c.new!r}" for o in ops for c in o.cells if c.category == "measurement")[:400], operations=ops)
        rr.operations = []
        if item is None:
            item = self._new_item(rr, ChangeClass.CONFLICTING_MEASUREMENT,
                                  f"{rec.source} {rec.source_record_id}: different values for the same observation in this run",
                                  "The same observation key appears more than once with different measurements",
                                  [option, ReviewOption(decision="REJECT", label="Reject all variants"),
                                   ReviewOption(decision="DEFER", label="Defer for later review")], recommended=None)
            self.items_by_key[key] = item
        else:
            item.options.insert(len(item.options) - 2, option)
            rr.review_item = item.item_id
            rr.classification = ChangeClass.CONFLICTING_MEASUREMENT
        rr.proposed_action = f"Hold for review (conflicting values within this run, item {item.item_id})"

    def _apply_review_issues(self, rec: SourceRecord, rr: RecordResult) -> None:
        reasons = [i for i in rec.issues if i.severity == "REVIEW"]
        if not reasons or rr.review_item or not rr.operations:
            return
        ops = rr.operations
        rr.operations = []
        codes = {i.code for i in reasons}
        recommended = 1 if "SUSPICIOUS_TEST_RECORD" in codes else (2 if codes & {"HISTORICAL_PERIOD_SURVEY",
                                                                                "BELOW_MIN_SURVEY_YEAR"} else None)
        self._new_item(rr, ChangeClass.REQUIRES_REVIEW, f"{rec.source} {rec.source_record_id}: needs a decision",
                       "; ".join(i.message for i in reasons),
                       [ReviewOption(decision="ACCEPT", label="Accept and apply", operations=ops,
                                     description=rr.proposed_action),
                        ReviewOption(decision="REJECT", label="Reject this source record"),
                        ReviewOption(decision="DEFER", label="Defer for later review")], recommended)

    def _remembered_decision(self, rr: RecordResult) -> None:
        if not rr.review_item:
            return
        entry = self.ledger.get(rr.observation_key)
        if not entry or entry.get("h") != rr.content_hash:
            return
        remembered = {"REJECTED": "REJECT", "KEPT_EXISTING": "KEEP_EXISTING"}.get(entry.get("s", ""))
        if remembered is None:
            return
        item = next((i for i in self.plan.review_items if i.item_id == rr.review_item), None)
        if item is None or item.decision is not None:
            return
        for idx, opt in enumerate(item.options):
            if opt.decision == remembered:
                item.decision = DecisionRecord(choice=remembered, option_index=idx, decided_by=entry.get("by", "previous review"),
                                               decided_at=entry.get("t", ""), remembered=True,
                                               reason=f"Remembered decision from plan {entry.get('p')} (identical source content)")
                rr.notes.append("Decision remembered from an earlier review of identical content")
                return

    # ------------------------------------------------------------------ summary
    def _summary(self) -> dict[str, Any]:
        by_class = {c.value: self.counts.get(c.value, 0) for c in ChangeClass}
        ops = [o for r in self.plan.records for o in r.operations]
        return {
            "plan_id": self.plan.plan_id,
            "base_version": self.plan.base_version,
            "base_rows": self.plan.base_rows,
            "records_total": sum(self.counts.values()),
            "by_classification": by_class,
            "by_source": {s: dict(c) for s, c in self.by_source.items()},
            "auto_operations": len(ops),
            "auto_inserts": sum(1 for o in ops if o.kind == "INSERT_ROW"),
            "auto_updates": sum(1 for o in ops if o.kind == "UPDATE_ROW"),
            "review_items": len(self.plan.review_items),
            "undecided_review_items": len(self.plan.undecided),
            "new_columns": [c.name for c in self.new_columns],
            "files": [{"file": s.file_name, "source": s.source, "format": s.format, "records": s.records,
                       "periods": s.periods} for s in self.plan.sources],
        }
