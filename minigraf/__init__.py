import json as _json
import uuid as _uuid

from .minigraf_ffi import (
    FactFilter,
    FactOrder,
    FactRecord,
    MiniGrafCursor,
    MiniGrafDb,
    MiniGrafError,
    MiniGrafFactLog,
    MiniGrafBindValue,
    MiniGrafLogWriter,
    MiniGrafPreparedQuery,
    MiniGrafValue,
    OpenOptions,
    SyncMode,
)

# Shorter names for the objects.
Cursor = MiniGrafCursor
FactLog = MiniGrafFactLog
LogWriter = MiniGrafLogWriter
Value = MiniGrafValue
BindValue = MiniGrafBindValue
PreparedQuery = MiniGrafPreparedQuery

#: ``valid_to`` of a fact that is valid forever.
VALID_TIME_FOREVER = 2**63 - 1

#: ``wal_checkpoint_threshold`` that never checkpoints: no automatic checkpoint
#: and none when the handle closes. Call ``checkpoint()`` yourself.
WAL_CHECKPOINT_NEVER = 2**63 - 1


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
    query_memory_limit=None,
):
    """Open a file-backed database. An option left as ``None`` keeps its default.

    ``minigraf.open("app.graph", read_only=True)`` opens the file read-only:
    any number of read-only handles can share it, and writes raise
    ``MiniGrafError`` with code API-014. ``query_memory_limit`` caps the bytes
    one query may hold; a query that would hold more raises QRY-012.
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
            query_memory_limit=query_memory_limit,
        ),
    )


# ─── Bind values for prepared queries (#181) ─────────────────────────────────


def Entity(entity):
    """Bind an entity (a UUID or its string). It also fits a value slot as a ref."""
    return MiniGrafBindValue.ENTITY(str(entity))


def TxCount(n):
    """Bind ``:as-of $slot`` to the transaction counter ``n``."""
    return MiniGrafBindValue.TX_COUNT(n)


def Timestamp(ms):
    """Bind ``:as-of $slot`` or ``:valid-at $slot`` to Unix milliseconds."""
    return MiniGrafBindValue.TIMESTAMP(ms)


#: Bind ``:valid-at $slot`` to every valid time, like ``:any-valid-time``.
ANY_VALID_TIME = MiniGrafBindValue.ANY_VALID_TIME()

_I64_MIN, _I64_MAX = -(2**63), 2**63 - 1


def _plain_value(name, v):
    """A plain Python value as a ``MiniGrafValue``, or ``None`` if it is none."""
    if isinstance(v, MiniGrafValue):
        return v
    # bool before int: True is an int in Python.
    if isinstance(v, bool):
        return MiniGrafValue.BOOL(v)
    if isinstance(v, int):
        if not _I64_MIN <= v <= _I64_MAX:
            raise MiniGrafError.Api(
                f"[API-017] invalid argument: slot ${name}: {v} is outside the 64-bit integer range"
            )
        return MiniGrafValue.INT64(v)
    if isinstance(v, float):
        return MiniGrafValue.FLOAT64(v)
    if isinstance(v, str):
        return MiniGrafValue.TEXT(v)
    if v is None:
        return MiniGrafValue.NULL()
    if isinstance(v, (set, frozenset)):
        return MiniGrafValue.SET([_set_element(name, e) for e in v])
    return None


def _set_element(name, e):
    """An element of a Python set as a ``MiniGrafValue``. A UUID is a ref.
    ``minigraf.Value`` is not hashable, so keyword elements need
    ``Value.SET([...])``."""
    value = _plain_value(name, e)
    if value is not None:
        return value
    if isinstance(e, _uuid.UUID):
        return MiniGrafValue.REF(str(e))
    raise TypeError(
        f"slot ${name}: cannot put a {type(e).__name__} in a set; use bool, int, float, "
        "str, None, uuid.UUID, or Value.SET([...]) for keywords"
    )


def _bind_value(name, v):
    """A plain Python value or a typed form as a ``MiniGrafBindValue``."""
    if isinstance(v, MiniGrafBindValue):
        return v
    value = _plain_value(name, v)
    if value is not None:
        return MiniGrafBindValue.VALUE(value)
    if isinstance(v, _uuid.UUID):
        return MiniGrafBindValue.ENTITY(str(v))
    raise TypeError(
        f"slot ${name}: cannot bind a {type(v).__name__}; use bool, int, float, str, "
        "None, set, frozenset, uuid.UUID, a minigraf.Value or "
        "minigraf.Entity/TxCount/Timestamp/ANY_VALID_TIME"
    )


def _bindings(mapping, kwargs):
    merged = dict(mapping or {})
    merged.update(kwargs)
    return {name: _bind_value(name, v) for name, v in merged.items()}


_prepared_execute = MiniGrafPreparedQuery.execute
_prepared_query = MiniGrafPreparedQuery.query


def _execute(self, bindings=None, /, **kwargs):
    """Run the query with its slots bound, from a dict and/or keyword
    arguments named without the ``$``; returns JSON like ``MiniGrafDb.execute``.
    Plain ``bool``, ``int``, ``float``, ``str``, ``None`` and ``uuid.UUID``
    values bind as they are."""
    return _prepared_execute(self, _bindings(bindings, kwargs))


def _query(self, bindings=None, /, **kwargs):
    """Run the query with its slots bound, as ``execute`` does, and return a
    cursor over its rows."""
    return _prepared_query(self, _bindings(bindings, kwargs))


MiniGrafPreparedQuery.execute = _execute
MiniGrafPreparedQuery.query = _query


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
    "ANY_VALID_TIME",
    "BindValue",
    "Cursor",
    "Entity",
    "FactFilter",
    "FactLog",
    "FactOrder",
    "FactRecord",
    "LogWriter",
    "MiniGrafCursor",
    "MiniGrafDb",
    "MiniGrafError",
    "MiniGrafFactLog",
    "MiniGrafBindValue",
    "MiniGrafLogWriter",
    "MiniGrafPreparedQuery",
    "MiniGrafValue",
    "OpenOptions",
    "PreparedQuery",
    "SyncMode",
    "Timestamp",
    "TxCount",
    "VALID_TIME_FOREVER",
    "WAL_CHECKPOINT_NEVER",
    "Value",
    "open",
]
