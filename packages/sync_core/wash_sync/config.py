"""Engine settings (validated with pydantic, stored as JSON in the private state dir)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .admin import DEFAULT_ALIASES

ENV_HOME = "WASH_SYNC_HOME"


def default_state_dir() -> str:
    return os.environ.get(ENV_HOME) or str(Path.home() / ".wash_sync")


class MatchingSettings(BaseModel):
    possible_match_radius_m: float = Field(30.0, ge=0, le=5000,
        description="GPS radius for proposing possible (never automatic) geographic matches")
    exact_coincidence_m: float = Field(1.0, ge=0, le=50,
        description="Distance treated as an exact coordinate coincidence")
    verify_gps_max_m: float = Field(100.0, ge=0, le=5000,
        description="Maximum distance for verifying a shared code between two sources")
    depth_tolerance_ratio: float = Field(0.10, ge=0, le=1)
    depth_tolerance_abs_m: float = Field(6.0, ge=0, le=100)
    auto_apply_verified_cross_source: bool = Field(True,
        description="Apply fully verified cross-source matches (shared code + admin + GPS + type + depth) without item review")


class ValidationSettings(BaseModel):
    mode: Literal["standard", "strict"] = "standard"
    #: Issue codes escalated from WARNING to ERROR (record rejected) in strict mode.
    strict_escalations: list[str] = Field(default_factory=lambda: [
        "GPS_INVALID", "MISSING_TUBEWELL_TYPE", "DEPTH_UNPARSEABLE", "SUSPICIOUS_WATER_TABLE",
        "ARSENIC_IMPLAUSIBLE", "IRON_IMPLAUSIBLE", "CHLORIDE_IMPLAUSIBLE", "NEGATIVE_MEASUREMENT",
        "UNREASONABLE_DEPTH", "CONFLICTING_DEPTH", "MISSING_ADMIN", "ADMIN_NAME_UNMAPPED",
        "WATER_TABLE_NON_NUMERIC", "SURVEY_PERIOD_PROBLEM",
    ])
    #: Source-aware completeness rules: logical fields whose absence is reported.
    completeness: dict[str, list[str]] = Field(default_factory=lambda: {
        "AQUAWATCH": ["water_table", "well_type", "latitude"],
        "GPANI": ["latitude", "facility_type"],
        "WPMS": ["latitude", "facility_type", "total_depth"],
        "BORELOG": ["latitude", "tw_type", "well_depth"],
    })
    min_survey_year: int = Field(2018, description="Survey years below this require review")


class PrivateStorage(BaseModel):
    enabled: bool = False
    destination: str | None = Field(None, description="Local or network folder (e.g. a synced private drive)")
    label: str | None = None


class Settings(BaseModel):
    state_dir: str = Field(default_factory=default_state_dir)
    warehouse_name: str = "WASH_Warehouse"
    source_dirs: dict[str, str] = Field(default_factory=dict)
    export_dir: str | None = None
    validation: ValidationSettings = Field(default_factory=ValidationSettings)
    matching: MatchingSettings = Field(default_factory=MatchingSettings)
    admin_aliases: list[dict[str, Any]] = Field(default_factory=lambda: [dict(a) for a in DEFAULT_ALIASES])
    backup_retention: int = Field(0, ge=0, description="Keep the newest N versions (0 = keep all). Baseline is always kept.")
    compression_level: int = Field(6, ge=1, le=9)
    private_storage: PrivateStorage = Field(default_factory=PrivateStorage)

    @field_validator("admin_aliases")
    @classmethod
    def _aliases(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for group in value:
            names = group.get("names")
            if not isinstance(names, list) or len(names) < 2:
                raise ValueError("each alias group needs at least two names")
            if group.get("level", "any") not in ("any", "division", "district", "upazila", "union_name", "village_name"):
                raise ValueError(f"invalid alias level {group.get('level')!r}")
        return value

    # ------------------------------------------------------------------ IO
    @property
    def path(self) -> str:
        return os.path.join(self.state_dir, "settings.json")

    @classmethod
    def load(cls, state_dir: str | None = None) -> "Settings":
        state_dir = state_dir or default_state_dir()
        path = os.path.join(state_dir, "settings.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            data["state_dir"] = state_dir
            return cls.model_validate(data)
        return cls(state_dir=state_dir)

    def save(self) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        from .state import atomic_write_text  # noqa: PLC0415

        atomic_write_text(self.path, self.model_dump_json(indent=2))

    def public_dict(self) -> dict[str, Any]:
        return self.model_dump()
