"""SQLite persistence: scan history + calibration of priors."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .scoring import Opportunity

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opportunities (
    scan_id INTEGER NOT NULL REFERENCES scans(id),
    ticker TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT,
    side TEXT NOT NULL,
    cost REAL NOT NULL,
    fair REAL NOT NULL,
    edge REAL NOT NULL,
    roi REAL NOT NULL,
    kelly REAL NOT NULL,
    days_to_resolve REAL NOT NULL,
    score REAL NOT NULL,
    prior_rationale TEXT,
    prior_confidence REAL,
    close_time TEXT,
    PRIMARY KEY (scan_id, ticker)
);

CREATE TABLE IF NOT EXISTS calibration (
    ticker TEXT PRIMARY KEY,
    predicted_p REAL NOT NULL,
    side TEXT NOT NULL,
    resolved_yes INTEGER,
    resolved_at TEXT
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as cx:
            cx.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        cx = sqlite3.connect(self.path)
        try:
            yield cx
            cx.commit()
        finally:
            cx.close()

    def record_scan(self, opportunities: Iterable[Opportunity]) -> int:
        now = datetime.now(tz=timezone.utc).isoformat()
        with self._conn() as cx:
            cur = cx.execute("INSERT INTO scans (run_at) VALUES (?)", (now,))
            scan_id = cur.lastrowid
            cx.executemany(
                """
                INSERT INTO opportunities (
                    scan_id, ticker, title, category, side, cost, fair,
                    edge, roi, kelly, days_to_resolve, score,
                    prior_rationale, prior_confidence, close_time
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        scan_id,
                        o.market.ticker,
                        o.market.title,
                        o.market.category,
                        o.side,
                        o.cost,
                        o.fair,
                        o.edge,
                        o.roi,
                        o.kelly_fraction,
                        o.days_to_resolve,
                        o.score,
                        o.prior.rationale,
                        o.prior.confidence,
                        o.market.close_time,
                    )
                    for o in opportunities
                ],
            )
            # also seed calibration rows so we can grade later
            cx.executemany(
                """
                INSERT OR IGNORE INTO calibration (ticker, predicted_p, side)
                VALUES (?, ?, ?)
                """,
                [(o.market.ticker, o.fair, o.side) for o in opportunities],
            )
        return int(scan_id)

    def grade(self, ticker: str, resolved_yes: bool) -> None:
        with self._conn() as cx:
            cx.execute(
                """
                UPDATE calibration
                SET resolved_yes = ?, resolved_at = ?
                WHERE ticker = ?
                """,
                (1 if resolved_yes else 0, datetime.now(tz=timezone.utc).isoformat(), ticker),
            )

    def brier(self) -> float | None:
        """Mean Brier score across graded calibration rows."""
        with self._conn() as cx:
            rows = cx.execute(
                """
                SELECT predicted_p, side, resolved_yes
                FROM calibration
                WHERE resolved_yes IS NOT NULL
                """
            ).fetchall()
        if not rows:
            return None
        total = 0.0
        for predicted_p, side, resolved_yes in rows:
            # `predicted_p` is the prob of the side we picked winning.
            picked_won = (
                (side == "YES" and resolved_yes == 1)
                or (side == "NO" and resolved_yes == 0)
            )
            outcome = 1.0 if picked_won else 0.0
            total += (predicted_p - outcome) ** 2
        return total / len(rows)
