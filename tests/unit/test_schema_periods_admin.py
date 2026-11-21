"""Survey periods, column naming/placement, well types and administrative aliases."""

import pytest
from wash_sync.adapters.base import normalize_well_type, parse_survey_period
from wash_sync.admin import DEFAULT_ALIASES, AdminResolver, AliasTable
from wash_sync.schema import (
    Period,
    WarehouseSchema,
    col_index,
    col_letter,
    final_column_order,
    parse_period_column,
)
from wash_sync.synthetic import WAREHOUSE_HEADERS
from wash_sync.text import norm_key


@pytest.mark.parametrize("label,period", [
    ("2026 (Wet Season)", Period(2026, "wet")), ("2026 (Dry Season)", Period(2026, "dry")), ("2025", Period(2025)),
    ("2024.0", Period(2024)), ("Dry 2027", Period(2027, "dry")), ("2027-wet", Period(2027, "wet")),
    ("2027 dry season", Period(2027, "dry")),
])
def test_period_labels(label, period):
    assert parse_survey_period(label) == (period, None)


@pytest.mark.parametrize("label", ["2026 (Monsoon)", "Spring 2027", "", "twenty", "2026 (Wet) (Dry)"])
def test_unknown_period_labels_are_rejected(label):
    period, problem = parse_survey_period(label)
    assert period is None and problem


def test_period_naming_conventions():
    dry = Period(2027, "dry")
    assert dry.columns() == ["aqua_2027_dry_arsenic_raw", "aqua_2027_dry_water_table_m", "aqua_2027_dry_iron_raw",
                             "aqua_2027_dry_chloride_raw", "aqua_2027_dry_source_row"]
    assert dry.source_token == "AQUA_2027_DRY" and dry.candidate_prefix == "AQUA2027DRY"
    annual = Period(2025)
    assert annual.column("arsenic_raw") == "aqua_2025_arsenic_raw"
    assert annual != Period(2025, "wet")  # 2025 unsuffixed is distinct from 2025 wet


def test_period_column_parsing_and_historical_columns_untouched():
    assert parse_period_column("aqua_2026_wet_iron_raw") == (Period(2026, "wet"), "iron_raw")
    assert parse_period_column("aqua_2025_source_row") == (Period(2025), "source_row")
    assert parse_period_column("aqua_1999") is None  # historical, definition unverified


def test_new_period_columns_are_placed_chronologically_before_borelog():
    schema = WarehouseSchema(headers=list(WAREHOUSE_HEADERS))
    planned = schema.plan_period_columns(Period(2028, "wet"), [])
    planned += schema.plan_period_columns(Period(2027, "dry"), planned)
    order = final_column_order(list(WAREHOUSE_HEADERS), planned)
    i_2026 = order.index("aqua_2026_wet_source_row")
    assert order[i_2026 + 1] == "aqua_2027_dry_arsenic_raw"
    assert order.index("aqua_2027_dry_source_row") < order.index("aqua_2028_wet_arsenic_raw")
    assert order.index("aqua_2028_wet_source_row") < order.index("borelog_id")
    assert len(order) == 196 + 10


def test_existing_periods_need_no_columns():
    schema = WarehouseSchema(headers=list(WAREHOUSE_HEADERS))
    assert schema.has_period(Period(2026, "dry"))
    assert schema.plan_period_columns(Period(2026, "dry"), []) == []


def test_column_letters():
    assert col_letter(196) == "GN" and col_index("GN") == 196 and col_letter(147) == "EQ"


@pytest.mark.parametrize("raw,key", [
    ("Deep", "DEEP"), ("Moderately Deep", "MODERATELY DEEP"), ("moderately-deep", "MODERATELY DEEP"),
    ("Deep Tube Well Other Pumps (Code 06)", "DEEP"), ("Shallow Tube Well No. 6 pump", "SHALLOW"),
    ("Rain Water Harvester", "RWH"), ("None", None), ("", None),
])
def test_well_types_keep_deep_and_moderately_deep_distinct(raw, key):
    assert normalize_well_type(raw)[0] == key


def test_validated_aliases_only():
    aliases = AliasTable.from_config(DEFAULT_ALIASES)
    assert aliases.key("division", "Chattogram") == aliases.key("division", "CHITTAGONG")
    assert aliases.key("division", "Barishal") == aliases.key("division", "BARISAL")
    assert aliases.key("district", "Bogura") == aliases.key("district", "Bogra")
    assert aliases.key("upazila", "Dakkhin Surma") == aliases.key("upazila", "DAKSHIN SURMA")
    assert aliases.key("district", "Comilla") != aliases.key("district", "Chandpur")


def test_resolver_uses_warehouse_spelling_and_learned_bangla():
    aliases = AliasTable.from_config(DEFAULT_ALIASES)
    resolver = AdminResolver(aliases, display={"division": {"CHITTAGONG": "CHITTAGONG"}},
                             bangla={"division": {norm_key("চট্টগ্রাম"): "CHITTAGONG"}})
    a = resolver.resolve({"division": "Chattogram", "district": "Feni"})
    assert a.resolved["division"] == "CHITTAGONG" and a.raw["division"] == "Chattogram"
    b = resolver.resolve({"division": "চট্টগ্রাম"})
    assert b.resolved["division"] == "CHITTAGONG" and not b.unmapped
    c = resolver.resolve({"division": "অজানা"})
    assert c.unmapped == ["division"]
    assert resolver.compare(c, {"division": "CHITTAGONG"})["division"] == "UNKNOWN"


def test_bangla_keys_keep_vowel_signs():
    # বরিশাল vs বরশাল differ only by a vowel sign; they must not collapse to one key
    assert norm_key("বরিশাল") != norm_key("বরশাল")
    assert norm_key("বরিশাল ") == norm_key("বরিশাল")
