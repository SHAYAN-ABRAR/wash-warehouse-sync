"""WASH Warehouse Sync — incremental, auditable synchronization for the WASH warehouse.

The engine keeps the published warehouse as a single-sheet ``.xlsx`` workbook and
never modifies a verified version in place: every synchronization produces a new,
validated, versioned workbook plus a complete audit trail.
"""

__version__ = "1.0.0"

#: Version of the warehouse/plan contract understood by this engine. Stored in every
#: plan, audit and version record so that outputs remain traceable.
SCHEMA_VERSION = "2026.11"

__all__ = ["__version__", "SCHEMA_VERSION"]
