"""Change-plan contract shared by the CLI, the local API and the web interface.

A plan is produced by a *dry run* (preview) against one exact base warehouse
version (pinned by SHA-256). It lists every proposed cell change with its evidence
and the review items that need a human decision. ``apply`` executes only:

* operations of auto-applicable classes (new facility, new observation, verified
  blank fills), and
* the operations of the option chosen for each decided review item,

and only when the plan is approved or the user explicitly authorizes it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from .records import ChangeClass

Decision = Literal["APPROVE_MATCH", "KEEP_SEPARATE", "ACCEPT_NEW_VALUES", "KEEP_EXISTING", "ACCEPT", "REJECT", "DEFER"]


class CellChange(BaseModel):
    column: str
    old: Any = None
    new: Any = None
    kind: Literal["number", "string"] = "string"
    action: Literal["FILL", "SET", "APPEND", "INSERT"] = "FILL"
    raw: str | None = None
    policy: str | None = None
    category: str | None = None


class Evidence(BaseModel):
    check: str
    value: Any = None
    result: Literal["AGREE", "DISAGREE", "UNKNOWN", "INFO", "MATCH", "NO_MATCH"] = "INFO"


class MatchInfo(BaseModel):
    level: Literal["L1_SOURCE_ID", "L2_CROSSWALK", "L2_VERIFIED_CROSS_SOURCE", "L3_GEOGRAPHIC", "WITHIN_RUN", "NONE"] = "NONE"
    confidence: Literal["EXACT", "VERIFIED", "POSSIBLE", "NONE"] = "NONE"
    category: Literal[
        "SAME_SOURCE_RECORD", "SAME_FACILITY", "SAME_FACILITY_NEW_PERIOD", "NEW_FACILITY", "POSSIBLE_DUPLICATE",
        "CONFLICTING_IDENTIFIERS", "INSUFFICIENT_EVIDENCE",
    ] = "NEW_FACILITY"
    candidate_rows: list[int] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    candidates: list[dict[str, Any]] = Field(default_factory=list)  # summaries for the Match Inspector
    evidence: list[Evidence] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    note: str | None = None

    def evidence_text(self) -> str:
        return "; ".join(f"{e.check}={e.value}:{e.result}" for e in self.evidence)


class Operation(BaseModel):
    op_id: str
    kind: Literal["UPDATE_ROW", "INSERT_ROW"]
    target_row: int | None = None
    target_candidate_id: str | None = None
    anchor_row: int | None = None
    anchor_explanation: str | None = None
    insert_key: str | None = None
    identity: str | None = None  # source identity, used to merge same-run records into a pending insert
    cells: list[CellChange] = Field(default_factory=list)
    crosswalk: dict[str, Any] | None = None  # link recorded when this operation is applied


class ReviewOption(BaseModel):
    decision: Decision
    label: str
    description: str = ""
    candidate_row: int | None = None
    operations: list[Operation] = Field(default_factory=list)


class DecisionRecord(BaseModel):
    choice: Decision
    option_index: int = 0
    reason: str
    decided_by: str
    decided_at: str
    remembered: bool = False


class ReviewItem(BaseModel):
    item_id: str
    rec_id: str
    classification: ChangeClass
    title: str
    reason: str
    options: list[ReviewOption]
    recommended: int | None = None
    decision: DecisionRecord | None = None


class RecordResult(BaseModel):
    rec_id: str
    source: str
    format: str
    source_file: str
    source_sheet: str
    source_row: int
    source_record_id: str | None
    observation_key: str
    content_hash: str
    period: str | None = None
    classification: ChangeClass
    match: MatchInfo = Field(default_factory=MatchInfo)
    issues: list[dict[str, Any]] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    preview: dict[str, Any] = Field(default_factory=dict)
    proposed_action: str = ""
    operations: list[Operation] = Field(default_factory=list)
    review_item: str | None = None
    notes: list[str] = Field(default_factory=list)
    duplicate_of: str | None = None


class SourceFileInfo(BaseModel):
    file_name: str
    sha256: str
    size: int
    source: str | None
    format: str | None
    sheet: str | None = None
    confidence: float = 0
    records: int = 0
    periods: list[str] = Field(default_factory=list)
    matched_columns: dict[str, str] = Field(default_factory=dict)
    missing_optional: list[str] = Field(default_factory=list)
    ignored_columns: list[str] = Field(default_factory=list)
    personal_data_columns_ignored: list[str] = Field(default_factory=list)
    completeness: dict[str, Any] = Field(default_factory=dict)


class NewColumnModel(BaseModel):
    name: str
    insert_after: str
    header_style: str | None = None
    width: float | None = None
    value_type: str = "auto"
    reason: str = ""


class Approval(BaseModel):
    approved_by: str
    approved_at: str
    plan_hash: str
    note: str | None = None


class Plan(BaseModel):
    plan_id: str
    created_at: str
    engine_version: str
    schema_version: str
    status: Literal["DRAFT", "APPROVED", "APPLIED", "SUPERSEDED", "NO_CHANGES"] = "DRAFT"
    base_version: int | None = None
    base_file: str | None = None
    base_sha256: str
    base_rows: int
    base_columns: int
    settings: dict[str, Any] = Field(default_factory=dict)
    sources: list[SourceFileInfo] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    new_columns: list[NewColumnModel] = Field(default_factory=list)
    records: list[RecordResult] = Field(default_factory=list)
    review_items: list[ReviewItem] = Field(default_factory=list)
    approval: Approval | None = None
    applied_version: int | None = None
    audit_dir: str | None = None
    elapsed_seconds: float | None = None

    def content_hash(self) -> str:
        """Hash of everything that determines the outcome (records, decisions, columns)."""
        payload = {
            "base": self.base_sha256,
            "columns": [c.model_dump() for c in self.new_columns],
            "records": [(r.rec_id, r.classification, [o.model_dump() for o in r.operations]) for r in self.records],
            "decisions": [(i.item_id, i.decision.choice if i.decision else None,
                           i.decision.option_index if i.decision else None) for i in self.review_items],
        }
        blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()

    def review_index(self) -> dict[str, ReviewItem]:
        return {i.item_id: i for i in self.review_items}

    @property
    def undecided(self) -> list[ReviewItem]:
        return [i for i in self.review_items if i.decision is None]
