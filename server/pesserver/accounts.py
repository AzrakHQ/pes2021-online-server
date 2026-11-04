"""Account store: SQLite, salted PBKDF2 passwords, opaque session tokens."""
import hashlib, os, secrets, sqlite3, threading, time


class AccountError(Exception):
    pass


class Accounts:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL COLLATE NOCASE,
            salt BLOB NOT NULL, pw BLOB NOT NULL, steam_id TEXT UNIQUE, created REAL NOT NULL,
            rating INTEGER NOT NULL DEFAULT 1000, wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0, draws INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions(
            token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created REAL NOT NULL, expires REAL NOT NULL);
        """)

    @staticmethod
    def _hash(password, salt):
        return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)

    def register(self, name, password, steam_id=None):
        if not (3 <= len(name) <= 24) or not name.replace("_", "").replace("-", "").isalnum():
            raise AccountError("name must be 3-24 chars: letters, digits, _ or -")
        if len(password) < 6:
            raise AccountError("password must be at least 6 characters")
        salt = os.urandom(16)
        try:
            with self.lock, self.db:
                cur = self.db.execute(
                    "INSERT INTO users(name,salt,pw,steam_id,created) VALUES(?,?,?,?,?)",
                    (name, salt, self._hash(password, salt), steam_id, time.time()))
        except sqlite3.IntegrityError:
            raise AccountError("name or steam id already registered")
        return cur.lastrowid

    def login(self, name, password, ttl=86400):
        with self.lock:
            row = self.db.execute("SELECT id,salt,pw FROM users WHERE name=?", (name,)).fetchone()
        if not row or not secrets.compare_digest(self._hash(password, row[1]), row[2]):
            raise AccountError("bad credentials")
        return self._new_session(row[0], ttl)

    def login_steam(self, steam_id, ttl=86400):
        """Trust-on-first-use by Steam ID (used when the game presents its Steam identity)."""
        with self.lock:
            row = self.db.execute("SELECT id FROM users WHERE steam_id=?", (str(steam_id),)).fetchone()
        if not row:
            raise AccountError("steam id not registered")
        return self._new_session(row[0], ttl)

    def _new_session(self, uid, ttl):
        tok = secrets.token_hex(16)
        now = time.time()
        with self.lock, self.db:
            self.db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (tok, uid, now, now + ttl))
        return tok

    def user_for_token(self, token):
        with self.lock:
            row = self.db.execute(
                "SELECT u.id,u.name,u.rating FROM sessions s JOIN users u ON u.id=s.user_id "
                "WHERE s.token=? AND s.expires>?", (token, time.time())).fetchone()
        return {"id": row[0], "name": row[1], "rating": row[2]} if row else None

    def user_by_id(self, uid):
        with self.lock:
            row = self.db.execute("SELECT id,name,rating,wins,losses,draws FROM users WHERE id=?", (uid,)).fetchone()
        return dict(zip(("id", "name", "rating", "wins", "losses", "draws"), row)) if row else None

    def record_result(self, winner_id, loser_id, draw=False, k=24):
        with self.lock, self.db:
            r = {i: self.db.execute("SELECT rating FROM users WHERE id=?", (i,)).fetchone()[0]
                 for i in (winner_id, loser_id)}
            ea = 1 / (1 + 10 ** ((r[loser_id] - r[winner_id]) / 400))
            s = 0.5 if draw else 1.0
            dw, dl = round(k * (s - ea)), round(k * ((1 - s) - (1 - ea)))
            col_w, col_l = ("draws", "draws") if draw else ("wins", "losses")
            self.db.execute(f"UPDATE users SET rating=rating+?, {col_w}={col_w}+1 WHERE id=?", (dw, winner_id))
            self.db.execute(f"UPDATE users SET rating=rating+?, {col_l}={col_l}+1 WHERE id=?", (dl, loser_id))
        return dw, dl
