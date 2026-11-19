"""Administrative-location normalization (division → district → upazila → union → village).

Only *validated* aliases are applied. Names are compared through ``norm_key`` (case,
punctuation and spacing insensitive) plus an explicit alias table; no fuzzy or
similarity-based merging is ever performed. Bangla names are translated only via
mappings learned from rows that already exist in the verified warehouse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .text import clean, has_bangla, norm_key

LEVELS = ("division", "district", "upazila", "union_name", "village_name")

#: Validated spelling aliases (official renames / transliteration variants). Each
#: group maps to one comparison key. Users can extend this list in settings.
DEFAULT_ALIASES: list[dict[str, Any]] = [
    {"level": "any", "names": ["CHITTAGONG", "CHATTOGRAM"]},
    {"level": "any", "names": ["BARISAL", "BARISHAL"]},
    {"level": "any", "names": ["BOGRA", "BOGURA"]},
    {"level": "any", "names": ["COMILLA", "CUMILLA"]},
    {"level": "any", "names": ["JESSORE", "JASHORE"]},
    {"level": "upazila", "names": ["DAKSHIN SURMA", "DAKKHIN SURMA"]},
    # Established transliteration variants of the same districts (validated spellings).
    {"level": "district", "names": ["KISHOREGANJ", "KISHOREGONJ"]},
    {"level": "district", "names": ["MOULVIBAZAR", "MAULVIBAZAR"]},
    {"level": "district", "names": ["NETROKONA", "NETRAKONA"]},
    {"level": "district", "names": ["JHALOKATHI", "JHALOKATI", "JHALAKATHI"]},
    {"level": "district", "names": ["CHAPAI NAWABGANJ", "CHAPAINAWABGANJ"]},
    {"level": "district", "names": ["BRAHMANBARIA", "BRAHMANBARIYA"]},
]


@dataclass(slots=True)
class AliasTable:
    """Maps ``norm_key`` values to a canonical comparison key per level."""

    by_level: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def from_config(cls, groups: Iterable[dict[str, Any]]) -> "AliasTable":
        table = cls()
        for group in groups:
            names = [norm_key(n) for n in group.get("names", []) if norm_key(n)]
            if len(names) < 2:
                continue
            canonical = names[0]
            levels = LEVELS if group.get("level", "any") == "any" else (group["level"],)
            for level in levels:
                bucket = table.by_level.setdefault(level, {})
                for name in names:
                    bucket[name] = canonical
        return table

    def key(self, level: str, value: Any) -> str:
        k = norm_key(value)
        return self.by_level.get(level, {}).get(k, k)


@dataclass(slots=True)
class AdminLocation:
    """Raw and resolved administrative names of one record."""

    raw: dict[str, str | None]
    resolved: dict[str, str | None]  # warehouse-convention display names (upper case)
    unmapped: list[str] = field(default_factory=list)  # levels with unmapped Bangla names

    def key(self, aliases: AliasTable) -> tuple[str, ...]:
        return tuple(aliases.key(level, self.resolved.get(level)) for level in LEVELS)


@dataclass
class AdminResolver:
    """Resolves source admin names to the warehouse's own spelling conventions.

    * ``display`` remembers the dominant warehouse spelling for each comparison key
      (so a new ``Chattogram`` row is written as the warehouse's ``CHITTAGONG``).
    * ``bangla`` holds Bangla→English mappings learned from existing verified rows
      (unambiguous, minimum support), e.g. WPMS ``বরিশাল`` → ``BARISAL``.
    """

    aliases: AliasTable
    display: dict[str, dict[str, str]] = field(default_factory=dict)
    bangla: dict[str, dict[str, str]] = field(default_factory=dict)

    def resolve(self, raw: dict[str, Any]) -> AdminLocation:
        raw_clean = {level: clean(raw.get(level)) for level in LEVELS}
        resolved: dict[str, str | None] = {}
        unmapped: list[str] = []
        for level in LEVELS:
            value = raw_clean[level]
            if value is None:
                resolved[level] = None
                continue
            if has_bangla(value):
                mapped = self.bangla.get(level, {}).get(norm_key(value))
                if mapped is None:
                    unmapped.append(level)
                    resolved[level] = value
                    continue
                value = mapped
            key = self.aliases.key(level, value)
            resolved[level] = self.display.get(level, {}).get(key) or value.upper()
        return AdminLocation(raw=raw_clean, resolved=resolved, unmapped=unmapped)

    def compare(self, a: AdminLocation, b_resolved: dict[str, Any]) -> dict[str, str]:
        """Per-level comparison: AGREE / DISAGREE / UNKNOWN (missing on either side)."""
        out: dict[str, str] = {}
        for level in LEVELS[:4]:
            left = self.aliases.key(level, a.resolved.get(level))
            right = self.aliases.key(level, b_resolved.get(level))
            if not left or not right or level in a.unmapped:
                out[level] = "UNKNOWN"
            else:
                out[level] = "AGREE" if left == right else "DISAGREE"
        return out
