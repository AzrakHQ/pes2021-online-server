"""The server's SQLite database: one connection shared by every module, and the lock they take to write.
(Each module creates its own pes_ tables; older databases may still hold unused users/sessions tables.)"""
import functools, sqlite3, threading


class Db(sqlite3.Connection):
    """`ready` names the modules whose tables were already checked on this connection (see once)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.ready = set()


def once(setup):
    """For a module's `_setup(db)`: create or upgrade its tables the first time, not on every command."""
    key = setup.__module__

    @functools.wraps(setup)
    def run(db):
        ready = getattr(db, "ready", None)
        if ready is not None and key in ready:
            return
        inside = db.in_transaction                   # a rollback would undo the tables: check again next time
        setup(db)
        if ready is not None and not inside:
            ready.add(key)
    return run


class Accounts:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False, factory=Db)
        self.lock = threading.Lock()
