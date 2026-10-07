import json as _json

from .minigraf_ffi import (
    FactFilter,
    FactOrder,
    FactRecord,
    MiniGrafCursor,
    MiniGrafDb,
    MiniGrafError,
    MiniGrafFactLog,
    MiniGrafLogWriter,
    MiniGrafValue,
    OpenOptions,
    SyncMode,
)

# Shorter names for the objects.
Cursor = MiniGrafCursor
FactLog = MiniGrafFactLog
LogWriter = MiniGrafLogWriter
Value = MiniGrafValue

#: ``valid_to`` of a fact that is valid forever.
VALID_TIME_FOREVER = 2**63 - 1


def open(
    path,
    *,
    read_only=None,
    page_cache_size=None,
    allow_unlocked=None,
    wal_checkpoint_threshold=None,
    max_derived_facts=None,
    max_results=None,
    synchronous=None,
):
    """Open a file-backed database. An option left as ``None`` keeps its default.

    ``minigraf.open("app.graph", read_only=True)`` opens the file read-only:
    any number of read-only handles can share it, and writes raise
    ``MiniGrafError`` with code API-014.
    """
    return MiniGrafDb.open_with_options(
        path,
        OpenOptions(
            read_only=read_only,
            page_cache_size=page_cache_size,
            allow_unlocked=allow_unlocked,
            wal_checkpoint_threshold=wal_checkpoint_threshold,
            max_derived_facts=max_derived_facts,
            max_results=max_results,
            synchronous=synchronous,
        ),
    )


def _close_on_exit(self, *exc):
    self.close()
    return False


def _enter(self):
    return self


def _cursor_iter(self):
    """Iterate over rows (lists of JSON-decoded values), 1,000 rows per batch."""
    while True:
        batch = self.next_batch(1000)
        if batch is None:
            return
        yield from _json.loads(batch)


def _fact_log_iter(self):
    """Iterate over ``FactRecord``s, 1,000 per batch."""
    while True:
        batch = self.next_batch(1000)
        if batch is None:
            return
        yield from batch


def _writer_exit(self, exc_type, exc, tb):
    """Finish on a clean exit; abandon the build (no file) on an exception."""
    if not self.is_open():
        return False
    if exc_type is None:
        self.finish()
    else:
        self.close()
    return False


MiniGrafCursor.__iter__ = _cursor_iter
MiniGrafCursor.__enter__ = _enter
MiniGrafCursor.__exit__ = _close_on_exit
MiniGrafFactLog.__iter__ = _fact_log_iter
MiniGrafFactLog.__enter__ = _enter
MiniGrafFactLog.__exit__ = _close_on_exit
MiniGrafLogWriter.__enter__ = _enter
MiniGrafLogWriter.__exit__ = _writer_exit

__all__ = [
    "Cursor",
    "FactFilter",
    "FactLog",
    "FactOrder",
    "FactRecord",
    "LogWriter",
    "MiniGrafCursor",
    "MiniGrafDb",
    "MiniGrafError",
    "MiniGrafFactLog",
    "MiniGrafLogWriter",
    "MiniGrafValue",
    "OpenOptions",
    "SyncMode",
    "VALID_TIME_FOREVER",
    "Value",
    "open",
]
