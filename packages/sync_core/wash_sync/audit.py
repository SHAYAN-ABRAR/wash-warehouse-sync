"""Audit trail writers.

Every preview and every apply writes a complete, private audit set:

    sync_summary.json        record_changes.csv     matched_resources.csv
    new_facilities.csv       skipped_duplicates.csv validation_errors.csv
    conflicts.csv            field_change_history.csv   report.html

Personal data columns are never read from the sources, so they cannot reach the
audit. Only the file *name* (not the path) of each input is recorded.
"""

from __future__ import annotations

import csv
import html
import json
import os
from typing import Any

from .plan import Plan, RecordResult, ReviewItem

FILES = {
    "record_changes": ["run_id", "stage", "rec_id", "source", "input_file", "input_sheet", "input_row", "source_record_id",
                       "observation_key", "content_hash", "existing_candidate_id", "existing_row", "match_level",
                       "match_confidence", "match_category", "match_evidence", "classification", "action_proposed",
                       "action_executed", "review_item", "decision", "reason", "destination_row", "status", "issues",
                       "flags"],
    "matched_resources": ["run_id", "source", "input_file", "input_row", "source_record_id", "candidate_id",
                          "warehouse_row", "match_level", "match_confidence", "match_evidence", "classification"],
    "new_facilities": ["run_id", "source", "input_file", "input_row", "source_record_id", "new_candidate_id", "division",
                       "district", "upazila", "union_name", "village_name", "latitude", "longitude", "anchor_row",
                       "placement", "destination_row", "status"],
    "skipped_duplicates": ["run_id", "source", "input_file", "input_row", "source_record_id", "observation_key",
                           "classification", "duplicate_of", "reason"],
    "validation_errors": ["run_id", "source", "input_file", "input_row", "source_record_id", "severity", "code",
                          "field", "message", "record_classification"],
    "conflicts": ["run_id", "item_id", "classification", "source", "input_file", "input_row", "source_record_id",
                  "candidate_ids", "candidate_rows", "conflicts", "evidence", "recommended", "decision", "decided_by",
                  "reason", "status"],
    "field_change_history": ["run_id", "stage", "source", "input_file", "input_row", "source_record_id",
                             "observation_key", "target_candidate_id", "destination_row", "column", "action",
                             "original_value", "new_value", "new_value_source_text", "policy", "category", "reason",
                             "status"],
}


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ";".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = str(value)
    # Neutralize spreadsheet formula injection when the CSV is opened in Excel.
    if text[:1] in ("=", "+", "-", "@") and not _looks_numeric(text):
        text = "'" + text
    return text


def _looks_numeric(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


class AuditWriter:
    def __init__(self, directory: str, run_id: str, stage: str) -> None:
        os.makedirs(directory, exist_ok=True)
        self.directory = directory
        self.run_id = run_id
        self.stage = stage
        self._fh: dict[str, Any] = {}
        self._w: dict[str, csv.DictWriter] = {}
        for name, fields in FILES.items():
            fh = open(os.path.join(directory, f"{name}.csv"), "w", encoding="utf-8-sig", newline="")
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            self._fh[name], self._w[name] = fh, writer

    def write(self, name: str, row: dict[str, Any]) -> None:
        self._w[name].writerow({k: _cell(v) for k, v in {"run_id": self.run_id, "stage": self.stage, **row}.items()})

    # ------------------------------------------------------------------ helpers
    def record(self, rr: RecordResult, executed: str, status: str, destination_row: int | None = None,
               decision: str | None = None, reason: str | None = None) -> None:
        self.write("record_changes", {
            "rec_id": rr.rec_id, "source": rr.source, "input_file": rr.source_file, "input_sheet": rr.source_sheet, "input_row": rr.source_row,
            "source_record_id": rr.source_record_id, "observation_key": rr.observation_key,
            "content_hash": rr.content_hash, "existing_candidate_id": ";".join(rr.match.candidate_ids[:5]),
            "existing_row": ";".join(str(r) for r in rr.match.candidate_rows[:5]), "match_level": rr.match.level,
            "match_confidence": rr.match.confidence, "match_category": rr.match.category,
            "match_evidence": rr.match.evidence_text()[:2000], "classification": rr.classification.value,
            "action_proposed": rr.proposed_action, "action_executed": executed, "review_item": rr.review_item,
            "decision": decision, "reason": reason or "; ".join(rr.notes)[:1000], "destination_row": destination_row,
            "status": status, "issues": ";".join(f"{i['severity']}:{i['code']}" for i in rr.issues),
            "flags": ";".join(rr.flags),
        })
        for issue in rr.issues:
            if issue["severity"] in ("ERROR", "WARNING", "REVIEW"):
                self.write("validation_errors", {
                    "source": rr.source, "input_file": rr.source_file, "input_row": rr.source_row,
                    "source_record_id": rr.source_record_id, "severity": issue["severity"], "code": issue["code"],
                    "field": issue.get("field"), "message": issue["message"],
                    "record_classification": rr.classification.value,
                })
        if rr.classification.value in ("UNCHANGED", "EXACT_DUPLICATE"):
            self.write("skipped_duplicates", {
                "source": rr.source, "input_file": rr.source_file, "input_row": rr.source_row,
                "source_record_id": rr.source_record_id, "observation_key": rr.observation_key,
                "classification": rr.classification.value, "duplicate_of": rr.duplicate_of or ";".join(rr.match.candidate_ids[:1]),
                "reason": "identical content already in the warehouse" if rr.classification.value == "UNCHANGED"
                else "identical observation appears earlier in this run",
            })
        if rr.match.candidate_rows and rr.match.category in ("SAME_FACILITY", "SAME_FACILITY_NEW_PERIOD"):
            self.write("matched_resources", {
                "source": rr.source, "input_file": rr.source_file, "input_row": rr.source_row,
                "source_record_id": rr.source_record_id, "candidate_id": rr.match.candidate_ids[0],
                "warehouse_row": rr.match.candidate_rows[0], "match_level": rr.match.level,
                "match_confidence": rr.match.confidence, "match_evidence": rr.match.evidence_text()[:2000],
                "classification": rr.classification.value,
            })

    def conflict(self, item: ReviewItem, rr: RecordResult, status: str) -> None:
        rec_opt = item.options[item.recommended].decision if item.recommended is not None else None
        self.write("conflicts", {
            "item_id": item.item_id, "classification": item.classification.value, "source": rr.source,
            "input_file": rr.source_file, "input_row": rr.source_row, "source_record_id": rr.source_record_id,
            "candidate_ids": rr.match.candidate_ids, "candidate_rows": rr.match.candidate_rows,
            "conflicts": rr.match.conflicts, "evidence": rr.match.evidence_text()[:2000], "recommended": rec_opt,
            "decision": item.decision.choice if item.decision else None,
            "decided_by": item.decision.decided_by if item.decision else None,
            "reason": item.decision.reason if item.decision else item.reason, "status": status,
        })

    def close(self, summary: dict[str, Any], plan: Plan | None = None) -> None:
        for fh in self._fh.values():
            fh.close()
        with open(os.path.join(self.directory, "sync_summary.json"), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, ensure_ascii=False, default=str)
        if plan is not None:
            with open(os.path.join(self.directory, "report.html"), "w", encoding="utf-8") as fh:
                fh.write(render_report(plan, summary, self.stage))


def render_report(plan: Plan, summary: dict[str, Any], stage: str) -> str:
    esc = html.escape
    counts = summary.get("by_classification", {})
    rows = "".join(f"<tr><td>{esc(k)}</td><td class=n>{v:,}</td></tr>" for k, v in counts.items())
    src_rows = "".join(
        f"<tr><td>{esc(s.file_name)}</td><td>{esc(str(s.source))}</td><td>{esc(str(s.format))}</td>"
        f"<td class=n>{s.records:,}</td><td>{esc(', '.join(s.periods))}</td><td><code>{esc(s.sha256[:16])}…</code></td></tr>"
        for s in plan.sources)
    cols = "".join(f"<li><code>{esc(c.name)}</code> — {esc(c.reason)}</li>" for c in plan.new_columns) or "<li>None</li>"
    items = "".join(
        f"<tr><td>{esc(i.item_id)}</td><td>{esc(i.classification.value)}</td><td>{esc(i.title)}</td>"
        f"<td>{esc(i.decision.choice if i.decision else 'UNDECIDED')}</td></tr>" for i in plan.review_items[:500])
    title = "Synchronization preview (dry run)" if stage == "preview" else "Synchronization applied"
    version = summary.get("output_version")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>WASH Warehouse Sync — {esc(plan.plan_id)}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font:14px/1.5 system-ui,sans-serif;margin:32px auto;max-width:1100px;padding:0 16px;color:#0f172a}}
h1{{font-size:22px}}h2{{font-size:16px;margin-top:28px}}table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #e2e8f0;padding:6px 8px;text-align:left}}td.n{{text-align:right;font-variant-numeric:tabular-nums}}
.badge{{display:inline-block;padding:2px 8px;border-radius:999px;background:#e0f2fe;color:#075985;font-size:12px}}
.note{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px}}</style></head><body>
<h1>{esc(title)}</h1>
<p><span class="badge">Plan {esc(plan.plan_id)}</span> <span class="badge">Status {esc(plan.status)}</span>
<span class="badge">Engine {esc(plan.engine_version)}</span></p>
<div class="note">Base warehouse version <b>{esc(str(plan.base_version))}</b> ({plan.base_rows:,} rows × {plan.base_columns} columns,
SHA-256 <code>{esc(plan.base_sha256[:16])}…</code>){f" → new version <b>{esc(str(version))}</b>" if version is not None else ""}.
Generated {esc(plan.created_at)}. This report is private: keep it with the local synchronization state.</div>
<h2>Classification summary</h2><table><tr><th>Classification</th><th>Records</th></tr>{rows}</table>
<h2>Input files</h2><table><tr><th>File</th><th>Source</th><th>Format</th><th>Records</th><th>Periods</th><th>SHA-256</th></tr>{src_rows}</table>
<h2>New columns</h2><ul>{cols}</ul>
<h2>Review items ({len(plan.review_items):,})</h2><table><tr><th>Item</th><th>Class</th><th>Summary</th><th>Decision</th></tr>{items}</table>
<p>Complete record-level detail: <code>record_changes.csv</code>, <code>field_change_history.csv</code>,
<code>conflicts.csv</code>, <code>validation_errors.csv</code>.</p></body></html>"""
