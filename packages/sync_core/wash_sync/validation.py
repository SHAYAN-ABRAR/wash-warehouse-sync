"""Source-aware validation: mode handling and completeness rules.

Adapters attach parse-level issues to each record. This module applies the
configured *policy*:

* ``standard`` — ERROR rejects the record; WARNING/INFO are reported only.
* ``strict``   — configured warning codes are escalated to ERROR.
* ``REVIEW``   — never rejects; routes an otherwise auto-applicable record to review.

Completeness rules are per source, because not every source measures everything
(bore logs, for example, have no confirmed water-table field).
"""

from __future__ import annotations

from .config import ValidationSettings
from .records import SourceRecord

#: How a logical completeness field is checked on a record.
COMPLETENESS_CHECKS = {
    "water_table": lambda r: any(c.endswith("_water_table_m") for c in r.fields),
    "well_type": lambda r: r.well_type is not None,
    "tw_type": lambda r: r.well_type is not None,
    "facility_type": lambda r: r.well_type is not None or bool(r.core.get("facility_type")),
    "latitude": lambda r: r.coords is not None and r.coords.usable,
    "total_depth": lambda r: r.depth_m is not None,
    "well_depth": lambda r: r.depth_m is not None,
}

SEVERITY_ORDER = {"ERROR": 0, "REVIEW": 1, "WARNING": 2, "INFO": 3}


def apply_policy(rec: SourceRecord, settings: ValidationSettings) -> None:
    strict = settings.mode == "strict"
    for issue in rec.issues:
        if strict and issue.severity == "WARNING" and issue.code in settings.strict_escalations:
            issue.severity = "ERROR"
            issue.message += " (rejected in strict mode)"
    for name in settings.completeness.get(rec.source, []):
        check = COMPLETENESS_CHECKS.get(name)
        if check is not None and not check(rec):
            code = f"INCOMPLETE_{name.upper()}"
            rec.issue(code, "ERROR" if strict else "INFO", f"Completeness rule: {name} is missing for this {rec.source} record",
                      name)
    if rec.period is not None and rec.period.year < settings.min_survey_year:
        rec.issue("BELOW_MIN_SURVEY_YEAR", "REVIEW",
                  f"Survey year {rec.period.year} is below the configured minimum {settings.min_survey_year}")
    rec.issues.sort(key=lambda i: SEVERITY_ORDER.get(i.severity, 9))


def review_reasons(rec: SourceRecord) -> list[str]:
    return [i.message for i in rec.issues if i.severity == "REVIEW"]
