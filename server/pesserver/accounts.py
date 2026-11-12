"""The server's SQLite database: one connection shared by every module, and the lock they take to write.
(Each module creates its own pes_ tables; older databases may still hold unused users/sessions tables.)"""
import sqlite3, threading


class Accounts:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
