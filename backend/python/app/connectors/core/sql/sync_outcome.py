"""What one SQL connector sync changed, for whatever runs after the sync.

A connector sets ``sync_outcome`` during each sync; anything that keeps data
derived from the tables reads it afterwards to know which tables to look at
again.
"""

from dataclasses import dataclass, field


@dataclass
class SyncOutcome:
    # Record ids of tables that are new, or whose name, columns or keys
    # changed: what was extracted about them no longer describes them.
    restructured: set[str] = field(default_factory=set)
    # Their table ids, collected while records are built: a table saved after
    # one that references it takes over the stand-in that table created, and
    # with it the stand-in's record id, so ids are only known once saved.
    restructured_tables: set[str] = field(default_factory=set)
    # Record ids of every table and column the sync removed.
    removed: set[str] = field(default_factory=set)
    # Record ids of tables whose relations may be out of date though nothing
    # else about them changed: one pointed at something removed, or only
    # names changed.
    relations_only: set[str] = field(default_factory=set)
    # Record ids of tables renamed, or with columns renamed: what was derived
    # from them is renamed in place.
    renamed: set[str] = field(default_factory=set)
