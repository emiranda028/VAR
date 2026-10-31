"""Persistencia en SQLite (sin dependencias externas)."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .model import HistoricalPlay

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
"""


class Database:
    def __init__(self, path: str | Path = "vargento.db"):
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)

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

    def get_referee_by_name(self, name: str) -> dict | None:
        """Busca un árbitro por nombre sin distinguir mayúsculas (incluye tildes: Á = á)."""
        buscado = name.strip().casefold()
        for row in self._conn.execute("SELECT * FROM referees"):
            if row["name"].casefold() == buscado:
                return dict(row)
        return None

    def count_referees(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM referees").fetchone()[0]

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

    def add_plays(self, plays: list[tuple]) -> None:
        """Carga masiva: tuplas (play_type, referee_id, features, called, description, match, date)."""
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO plays (play_type, referee_id, features, called, description, match, date)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(t, r, json.dumps(f), int(c), d, m, dt) for t, r, f, c, d, m, dt in plays],
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

    def descriptions_for(self, play_type: str, match: str, date: str) -> list[str]:
        """Descripciones de las jugadas ya cargadas con ese tipo, partido y fecha."""
        rows = self._conn.execute(
            "SELECT description FROM plays WHERE play_type = ? AND match = ? AND date = ?",
            (play_type, match, date),
        )
        return [r[0] for r in rows]

    def import_plays(self, plays: list[tuple]) -> int:
        """Carga jugadas con el NOMBRE del árbitro, creando los que falten.

        Tuplas (play_type, referee_name, features, called, description, match, date).
        Todo ocurre en una sola transacción: si algo falla, no queda nada a medias.
        """
        with self._lock, self._conn:
            ids = {r["name"].casefold(): r["id"] for r in self._conn.execute("SELECT id, name FROM referees")}
            filas = []
            for t, nombre, f, c, d, m, dt in plays:
                clave = nombre.strip().casefold()
                if clave not in ids:
                    cur = self._conn.execute("INSERT INTO referees (name) VALUES (?)", (nombre.strip(),))
                    ids[clave] = cur.lastrowid
                filas.append((t, ids[clave], json.dumps(f), int(c), d, m, dt))
            self._conn.executemany(
                "INSERT INTO plays (play_type, referee_id, features, called, description, match, date)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                filas,
            )
        return len(filas)

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
