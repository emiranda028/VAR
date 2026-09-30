"""Persistencia en SQLite (sin dependencias externas)."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .model import HistoricalPlay, ModelParams

SCHEMA = """
CREATE TABLE IF NOT EXISTS referees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    country TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS plays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    play_type TEXT NOT NULL,
    referee_id INTEGER NOT NULL REFERENCES referees(id),
    features TEXT NOT NULL,
    called INTEGER NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    match TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS plays_type_idx ON plays(play_type);
CREATE INDEX IF NOT EXISTS plays_referee_idx ON plays(referee_id);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    referee_id INTEGER REFERENCES referees(id),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    user_id INTEGER REFERENCES users(id),
    referee_id INTEGER REFERENCES referees(id),
    play_type TEXT NOT NULL,
    features TEXT NOT NULL,
    probability REAL NOT NULL,
    recommendation TEXT NOT NULL,
    final_called INTEGER,
    decided_at TEXT,
    decided_by INTEGER REFERENCES users(id),
    play_id INTEGER REFERENCES plays(id)
);
CREATE INDEX IF NOT EXISTS reviews_created_idx ON reviews(created_at);
CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    play_id INTEGER REFERENCES plays(id) ON DELETE CASCADE,
    review_id INTEGER REFERENCES reviews(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,              -- 'file' (subido) o 'link' (enlace externo)
    mime TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL DEFAULT '',   -- archivo dentro de la carpeta de medios
    url TEXT NOT NULL DEFAULT '',
    start_seconds REAL NOT NULL DEFAULT 0,
    caption TEXT NOT NULL DEFAULT '',
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS media_play_idx ON media(play_id);
CREATE INDEX IF NOT EXISTS media_review_idx ON media(review_id);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

USER_COLUMNS = "u.id, u.username, u.role, u.referee_id, u.active, u.created_at, r.name AS referee"


class Database:
    def __init__(self, path: str | Path = "vargento.db"):
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._migrate_roles()

    def _migrate_roles(self) -> None:
        from .auth import LEGACY_ROLES

        with self._lock, self._conn:
            for old, new in LEGACY_ROLES.items():
                self._conn.execute("UPDATE users SET role = ?, referee_id = NULL WHERE role = ?", (new, old))

    def close(self) -> None:
        self._conn.close()

    # --- árbitros -------------------------------------------------------
    def add_referee(self, name: str, country: str = "") -> dict:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO referees (name, country) VALUES (?, ?)", (name.strip(), country.strip())
            )
        return {"id": cur.lastrowid, "name": name.strip(), "country": country.strip()}

    def get_referee(self, referee_id: int) -> dict | None:
        row = self._conn.execute("SELECT * FROM referees WHERE id = ?", (referee_id,)).fetchone()
        return dict(row) if row else None

    def find_referee(self, name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM referees WHERE lower(name) = lower(?)", (name.strip(),)
        ).fetchone()
        return dict(row) if row else None

    def list_referees(self) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT r.id, r.name, r.country, COUNT(p.id) AS plays
            FROM referees r LEFT JOIN plays p ON p.referee_id = r.id
            GROUP BY r.id ORDER BY r.name
            """
        ).fetchall()
        return [dict(r) for r in rows]

    # --- jugadas --------------------------------------------------------
    def add_play(
        self,
        play_type: str,
        referee_id: int,
        features: dict[str, float],
        called: bool,
        description: str = "",
        match: str = "",
        date: str = "",
    ) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO plays (play_type, referee_id, features, called, description, match, date)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (play_type, referee_id, json.dumps(features), int(called), description, match, date),
            )
        return cur.lastrowid

    def add_plays(self, plays: list[tuple], new_referees: list[tuple[str, str]] = ()) -> None:
        """Carga masiva en una sola transacción.

        `plays` son tuplas (play_type, referee_id, features, called, description, match, date[, (url, inicio)]).
        `referee_id` puede ser un nombre de `new_referees`, que se crean en la misma transacción.
        """
        with self._lock, self._conn:
            ids = {}
            for name, country in new_referees:
                cur = self._conn.execute("INSERT INTO referees (name, country) VALUES (?, ?)", (name, country))
                ids[name] = cur.lastrowid
            for play in plays:
                t, r, f, c, d, m, dt = play[:7]
                cur = self._conn.execute(
                    "INSERT INTO plays (play_type, referee_id, features, called, description, match, date)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (t, ids.get(r, r), json.dumps(f), int(c), d, m, dt),
                )
                link = play[7] if len(play) > 7 else None  # (url, segundo de inicio)
                if link:
                    self._conn.execute(
                        "INSERT INTO media (play_id, kind, url, start_seconds) VALUES (?, 'link', ?, ?)",
                        (cur.lastrowid, link[0], link[1]),
                    )

    def history(self, play_type: str | None = None) -> list[HistoricalPlay]:
        sql = "SELECT * FROM plays"
        args: tuple = ()
        if play_type:
            sql += " WHERE play_type = ?"
            args = (play_type,)
        return [
            HistoricalPlay(
                id=r["id"],
                play_type=r["play_type"],
                referee_id=r["referee_id"],
                features=json.loads(r["features"]),
                called=bool(r["called"]),
                description=r["description"],
                match=r["match"],
                date=r["date"],
            )
            for r in self._conn.execute(sql, args)
        ]

    def list_plays(self, limit: int = 50, referee_id: int | None = None) -> list[dict]:
        sql = "SELECT p.*, r.name AS referee FROM plays p JOIN referees r ON r.id = p.referee_id"
        args: list = []
        if referee_id is not None:
            sql += " WHERE p.referee_id = ?"
            args.append(referee_id)
        sql += " ORDER BY p.date DESC, p.id DESC LIMIT ?"
        args.append(limit)
        out = []
        for r in self._conn.execute(sql, args):
            d = dict(r)
            d["features"] = json.loads(d["features"])
            d["called"] = bool(d["called"])
            out.append(d)
        return out

    def count_plays(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM plays").fetchone()[0]

    def referee_stats(self, referee_id: int) -> list[dict]:
        """Tasa de cobro del árbitro por tipo de jugada vs. el promedio general."""
        rows = self._conn.execute(
            """
            SELECT play_type,
                   COUNT(*) AS total,
                   SUM(called) AS called,
                   SUM(CASE WHEN referee_id = ? THEN 1 ELSE 0 END) AS ref_total,
                   SUM(CASE WHEN referee_id = ? THEN called ELSE 0 END) AS ref_called
            FROM plays GROUP BY play_type ORDER BY play_type
            """,
            (referee_id, referee_id),
        ).fetchall()
        return [
            {
                "play_type": r["play_type"],
                "plays": r["ref_total"],
                "rate": (r["ref_called"] / r["ref_total"]) if r["ref_total"] else None,
                "global_rate": r["called"] / r["total"] if r["total"] else None,
            }
            for r in rows
        ]

    # --- configuración del modelo ---------------------------------------
    def get_model_params(self) -> ModelParams:
        row = self._conn.execute("SELECT value FROM settings WHERE key = 'model_params'").fetchone()
        return ModelParams.from_dict(json.loads(row[0]) if row else None)

    def set_model_params(self, params: ModelParams) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES ('model_params', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps(params.to_dict()),),
            )

    # --- usuarios y sesiones --------------------------------------------
    def count_users(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def add_user(self, username: str, password_hash: str, role: str, referee_id: int | None = None) -> dict:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO users (username, password_hash, role, referee_id) VALUES (?, ?, ?, ?)",
                (username.strip(), password_hash, role, referee_id),
            )
        return self.get_user(cur.lastrowid)

    def get_user(self, user_id: int) -> dict | None:
        row = self._conn.execute(
            f"SELECT {USER_COLUMNS} FROM users u LEFT JOIN referees r ON r.id = u.referee_id WHERE u.id = ?",
            (user_id,),
        ).fetchone()
        return _user(row)

    def get_user_credentials(self, username: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, password_hash, active FROM users WHERE username = ?", (username.strip(),)
        ).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict]:
        rows = self._conn.execute(
            f"SELECT {USER_COLUMNS} FROM users u LEFT JOIN referees r ON r.id = u.referee_id ORDER BY u.username"
        ).fetchall()
        return [_user(r) for r in rows]

    def update_user(self, user_id: int, **changes) -> dict | None:
        allowed = {"role", "referee_id", "active", "password_hash"}
        sets = {k: v for k, v in changes.items() if k in allowed}
        if sets:
            cols = ", ".join(f"{k} = ?" for k in sets)
            with self._lock, self._conn:
                self._conn.execute(f"UPDATE users SET {cols} WHERE id = ?", (*sets.values(), user_id))
                if sets.get("active") == 0 or "password_hash" in sets:
                    self._conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return self.get_user(user_id)

    def create_session(self, token_hash: str, user_id: int, expires_at: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM sessions WHERE expires_at < datetime('now')")
            self._conn.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (token_hash, user_id, expires_at),
            )

    def session_user(self, token_hash: str) -> dict | None:
        row = self._conn.execute(
            f"""SELECT {USER_COLUMNS} FROM sessions s
                JOIN users u ON u.id = s.user_id
                LEFT JOIN referees r ON r.id = u.referee_id
                WHERE s.token_hash = ? AND s.expires_at > datetime('now') AND u.active = 1""",
            (token_hash,),
        ).fetchone()
        return _user(row)

    def delete_session(self, token_hash: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    # --- revisiones -----------------------------------------------------
    def add_review(
        self,
        user_id: int | None,
        referee_id: int | None,
        play_type: str,
        features: dict[str, float],
        probability: float,
        recommendation: str,
    ) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO reviews (user_id, referee_id, play_type, features, probability, recommendation)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, referee_id, play_type, json.dumps(features), probability, recommendation),
            )
        return cur.lastrowid

    def get_review(self, review_id: int) -> dict | None:
        row = self._conn.execute(
            """SELECT v.*, r.name AS referee, u.username AS user FROM reviews v
               LEFT JOIN referees r ON r.id = v.referee_id
               LEFT JOIN users u ON u.id = v.user_id
               WHERE v.id = ?""",
            (review_id,),
        ).fetchone()
        return _review(row)

    def list_reviews(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            """SELECT v.*, r.name AS referee, u.username AS user FROM reviews v
               LEFT JOIN referees r ON r.id = v.referee_id
               LEFT JOIN users u ON u.id = v.user_id
               ORDER BY v.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [_review(r) for r in rows]

    def decide_review(
        self,
        review_id: int,
        called: bool,
        user_id: int | None,
        referee_id: int,
        description: str = "",
        match: str = "",
        date: str = "",
    ) -> dict | None:
        """Guarda la decisión final y suma la jugada al histórico (en una transacción).

        Devuelve None si la revisión no existe o ya tenía decisión.
        """
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM reviews WHERE id = ? AND final_called IS NULL", (review_id,)
            ).fetchone()
            if not row:
                return None
            cur = self._conn.execute(
                "INSERT INTO plays (play_type, referee_id, features, called, description, match, date)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row["play_type"], referee_id, row["features"], int(called), description, match, date),
            )
            self._conn.execute("UPDATE media SET play_id = ? WHERE review_id = ?", (cur.lastrowid, review_id))
            self._conn.execute(
                "UPDATE reviews SET final_called = ?, decided_at = datetime('now'), decided_by = ?,"
                " play_id = ?, referee_id = ? WHERE id = ?",
                (int(called), user_id, cur.lastrowid, referee_id, review_id),
            )
        return self.get_review(review_id)

    # --- videos y fotos -------------------------------------------------
    def add_media(
        self,
        kind: str,
        created_by: int | None,
        play_id: int | None = None,
        review_id: int | None = None,
        mime: str = "",
        path: str = "",
        url: str = "",
        start_seconds: float = 0,
        caption: str = "",
    ) -> dict:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO media (play_id, review_id, kind, mime, path, url, start_seconds, caption, created_by)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (play_id, review_id, kind, mime, path, url, start_seconds, caption, created_by),
            )
        return self.get_media(cur.lastrowid)

    def get_media(self, media_id: int) -> dict | None:
        row = self._conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
        return dict(row) if row else None

    def media_for(self, play_ids: list[int] = (), review_id: int | None = None) -> list[dict]:
        clauses, args = [], []
        if play_ids:
            clauses.append(f"play_id IN ({','.join('?' * len(play_ids))})")
            args += list(play_ids)
        if review_id is not None:
            clauses.append("review_id = ?")
            args.append(review_id)
        if not clauses:
            return []
        rows = self._conn.execute(f"SELECT * FROM media WHERE {' OR '.join(clauses)} ORDER BY id", args)
        return [dict(r) for r in rows]

    def delete_media(self, media_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM media WHERE id = ?", (media_id,))

    def get_play(self, play_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT p.*, r.name AS referee FROM plays p JOIN referees r ON r.id = p.referee_id WHERE p.id = ?",
            (play_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["features"] = json.loads(d["features"])
        d["called"] = bool(d["called"])
        return d

    def count_pending_reviews(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM reviews WHERE final_called IS NULL").fetchone()[0]

    def review_outcomes(self) -> list[tuple[float, bool, str]]:
        """(probabilidad dada, decisión final, recomendación) de las revisiones ya decididas."""
        rows = self._conn.execute(
            "SELECT probability, final_called, recommendation FROM reviews WHERE final_called IS NOT NULL"
        ).fetchall()
        return [(r[0], bool(r[1]), r[2]) for r in rows]


def _user(row) -> dict | None:
    if not row:
        return None
    d = dict(row)
    d["active"] = bool(d["active"])
    return d


def _review(row) -> dict | None:
    if not row:
        return None
    d = dict(row)
    d["features"] = json.loads(d["features"])
    if d["final_called"] is not None:
        d["final_called"] = bool(d["final_called"])
    return d
