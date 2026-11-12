"""Write transactions (minigraf#824): begin, execute, commit or roll back."""

import json
import threading

import pytest

import minigraf
from minigraf import MiniGrafDb, MiniGrafError, Predicate, SyncMode

NAMES = "(query [:find ?n :where [?e :person/name ?n]])"
SETUP = '(transact [[:alice :person/name "Alice"] [:bob :person/name "Bob"]])'


def names(result):
    return sorted(row[0] for row in json.loads(result)["results"])


def code(err):
    return str(err).split("[", 1)[1][:7]


@pytest.fixture
def db():
    db = MiniGrafDb.open_in_memory()
    db.execute(SETUP)
    return db


def test_commit_reads_its_own_writes(tmp_path):
    path = str(tmp_path / "g.graph")
    db = minigraf.open(path)
    db.execute(SETUP)
    tx = db.begin_write()
    assert json.loads(tx.execute('(transact [[:carol :person/name "Carol"]])')) == {"ok": True}
    tx.execute('(retract [[:bob :person/name "Bob"]])')
    assert names(tx.execute(NAMES)) == ["Alice", "Carol"]

    # Another thread reads the committed state meanwhile.
    seen = []
    t = threading.Thread(target=lambda: seen.append(names(db.execute(NAMES))))
    t.start()
    t.join(30)
    assert seen == [["Alice", "Bob"]]

    tx.commit()
    assert names(db.execute(NAMES)) == ["Alice", "Carol"]
    del tx, db
    assert names(minigraf.open(path).execute(NAMES)) == ["Alice", "Carol"]


def test_rollback(db):
    tx = db.begin_write()
    tx.execute('(transact [[:carol :person/name "Carol"]])')
    tx.rollback()
    assert names(db.execute(NAMES)) == ["Alice", "Bob"]


def test_with_block_rolls_back_unless_committed(db):
    with db.begin_write() as tx:
        tx.execute('(transact [[:carol :person/name "Carol"]])')
    assert names(db.execute(NAMES)) == ["Alice", "Bob"], "no commit on a clean exit"

    with pytest.raises(RuntimeError):
        with db.begin_write() as tx:
            tx.execute('(transact [[:dave :person/name "Dave"]])')
            raise RuntimeError("boom")
    assert names(db.execute(NAMES)) == ["Alice", "Bob"], "rolled back on an exception"
    db.execute('(transact [[:erin :person/name "Erin"]])')  # the lock is free

    with db.begin_write() as tx:
        tx.execute('(transact [[:frank :person/name "Frank"]])')
        tx.commit()
    assert names(db.execute(NAMES)) == ["Alice", "Bob", "Erin", "Frank"]


def test_finished_transaction_is_api018(db):
    tx = db.begin_write()
    tx.commit()
    with pytest.raises(MiniGrafError) as e:
        tx.execute(NAMES)
    assert code(e.value) == "API-018"
    with pytest.raises(MiniGrafError) as e:
        tx.commit()
    assert code(e.value) == "API-018"
    tx.rollback()  # does nothing
    assert not tx.is_open()


def test_failed_statement_stays_open(db):
    tx = db.begin_write()
    with pytest.raises(MiniGrafError) as e:
        tx.execute("(transact [[:a")
    assert code(e.value) == "PRS-004"
    tx.execute('(transact [[:carol :person/name "Carol"]])')
    tx.commit()
    assert names(db.execute(NAMES)) == ["Alice", "Bob", "Carol"]


def test_begin_thread_gets_int001(db):
    with db.begin_write() as tx:
        with pytest.raises(MiniGrafError) as e:
            db.execute(NAMES)
        assert code(e.value) == "INT-001"
        tx.commit()
    db.execute(NAMES)


def test_options(tmp_path, db):
    with pytest.raises(MiniGrafError) as e:
        db.begin_write(chunk_facts=-1)
    assert code(e.value) == "API-017"

    path = str(tmp_path / "g.graph")
    file_db = minigraf.open(path)
    with file_db.begin_write(synchronous=SyncMode.NORMAL, chunk_facts=2) as tx:
        for i in range(5):
            tx.execute(f'(transact [[:p{i} :person/name "P{i}"]])')
        tx.commit()
    del tx, file_db
    assert len(names(minigraf.open(path).execute(NAMES))) == 5

    ro = minigraf.open(path, read_only=True)
    with pytest.raises(MiniGrafError) as e:
        ro.begin_write()
    assert code(e.value) == "API-014"


def test_user_function_calling_its_own_transaction_gets_int001(db):
    """minigraf#886: such a call waited on the transaction forever."""
    seen = []

    class CallsItsTx(Predicate):
        def __init__(self, tx):
            self.tx = tx

        def test(self, value):
            for call in (lambda: self.tx.execute(NAMES), self.tx.commit, self.tx.rollback):
                try:
                    call()
                    seen.append("no error")
                except MiniGrafError as e:
                    seen.append(code(e))
            seen.append(self.tx.is_open())
            return True

    tx = db.begin_write()
    db.register_predicate("calls-tx?", CallsItsTx(tx))
    query = "(query [:find ?n :where [?e :person/name ?n] [(calls-tx? ?n)]])"
    assert names(tx.execute(query)) == ["Alice", "Bob"]
    assert seen == ["INT-001", "INT-001", "INT-001", True] * 2
    # The refused commit and rollback left it open.
    assert tx.is_open()
    tx.execute('(transact [[:carol :person/name "Carol"]])')
    tx.commit()
    assert names(db.execute(NAMES)) == ["Alice", "Bob", "Carol"]
