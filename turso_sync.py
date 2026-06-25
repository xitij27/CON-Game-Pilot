"""
Turso backup/restore for the local SQLite database.

On cold start (no local DB file): call restore_from_turso() to pull the last
backup down from Turso and write it into a fresh local SQLite.

While running: run_periodic_backup() syncs local → Turso every N seconds so
the backup stays fresh between deploys.

All Turso communication goes through the HTTP pipeline API — no native library
required beyond aiohttp.
"""

import asyncio
import os

import aiohttp
import aiosqlite

TURSO_URL: str = os.getenv("TURSO_DATABASE_URL", "").replace("libsql://", "https://").rstrip("/")
TURSO_TOKEN: str = os.getenv("TURSO_AUTH_TOKEN", "")

# FK-safe ordering
_INSERT_ORDER = ["matches", "settings", "registrations"]
_DELETE_ORDER = ["registrations", "settings", "matches"]

# Mirrors the full schema in database.py (CREATE TABLE IF NOT EXISTS is idempotent)
_SCHEMA_STMTS = [
    """CREATE TABLE IF NOT EXISTS matches (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id        INTEGER UNIQUE,
        guild_id          INTEGER NOT NULL,
        leader_id         INTEGER NOT NULL,
        game_type         TEXT NOT NULL,
        region            TEXT NOT NULL,
        status            TEXT DEFAULT 'open',
        game_code         TEXT,
        roster_message_id INTEGER,
        hub_message_id    INTEGER,
        event_id          INTEGER,
        created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS registrations (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id          INTEGER NOT NULL,
        user_id           INTEGER NOT NULL,
        primary_country   TEXT NOT NULL,
        secondary_country TEXT,
        military_role     TEXT NOT NULL,
        squad_role        TEXT NOT NULL,
        message_id        INTEGER,
        status            TEXT DEFAULT 'pending',
        registered_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (match_id) REFERENCES matches(id),
        UNIQUE(match_id, user_id)
    )""",
    """CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
]

_BATCH_SIZE = 100


def is_enabled() -> bool:
    return bool(TURSO_URL and TURSO_TOKEN)


def _arg(val) -> dict:
    """Convert a Python value to a Turso HTTP API argument object."""
    if val is None:
        return {"type": "null"}
    if isinstance(val, int):
        return {"type": "integer", "value": str(val)}
    if isinstance(val, float):
        return {"type": "float", "value": str(val)}
    return {"type": "text", "value": str(val)}


async def _pipeline(session: aiohttp.ClientSession, stmts: list[dict]) -> list[dict]:
    """Send a batch of SQL statements to Turso and return the results list."""
    requests = [{"type": "execute", "stmt": s} for s in stmts]
    async with session.post(
        f"{TURSO_URL}/v2/pipeline",
        headers={
            "Authorization": f"Bearer {TURSO_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"requests": requests},
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return data["results"]


def _parse_result(result: dict) -> tuple[list[str], list[tuple]]:
    """Extract (column_names, rows) from a Turso execute result."""
    r = result["response"]["result"]
    cols = [c["name"] for c in r["cols"]]
    rows = []
    for turso_row in r["rows"]:
        row = tuple(
            int(cell["value"]) if cell["type"] == "integer"
            else float(cell["value"]) if cell["type"] == "float"
            else None if cell["type"] == "null"
            else cell["value"]
            for cell in turso_row
        )
        rows.append(row)
    return cols, rows


async def restore_from_turso(db_path: str) -> bool:
    """
    Pull all data from Turso into an already-initialised local SQLite file.
    Returns True if any rows were restored, False if empty or unavailable.
    """
    if not is_enabled():
        print("[turso] Credentials not set — skipping restore.", flush=True)
        return False

    try:
        async with aiohttp.ClientSession() as session:
            results = await _pipeline(
                session,
                [{"sql": f"SELECT * FROM {t}"} for t in _INSERT_ORDER],
            )

        total = 0
        async with aiosqlite.connect(db_path) as local:
            for table, result in zip(_INSERT_ORDER, results):
                if result.get("type") != "ok":
                    continue
                cols, rows = _parse_result(result)
                if not rows:
                    continue
                col_names = ",".join(cols)
                placeholders = ",".join(["?"] * len(cols))
                await local.executemany(
                    f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})",
                    rows,
                )
                total += len(rows)
            await local.commit()

        if total:
            print(f"[turso] Restored {total} rows from backup.", flush=True)
            return True
        else:
            print("[turso] Backup was empty — starting fresh.", flush=True)
            return False

    except Exception as exc:
        print(f"[turso] Restore failed: {exc}", flush=True)
        return False


async def backup_to_turso(db_path: str) -> None:
    """
    Full overwrite of Turso with current local SQLite contents.
    Schema is created on Turso if it doesn't exist yet.
    """
    if not is_enabled():
        return

    try:
        # Read everything from local first
        local_data: dict[str, tuple[list[str], list]] = {}
        async with aiosqlite.connect(db_path) as local:
            for table in _INSERT_ORDER:
                async with local.execute(f"SELECT * FROM {table}") as cur:
                    rows = await cur.fetchall()
                    cols = [d[0] for d in (cur.description or [])]
                    local_data[table] = (cols, list(rows))

        async with aiohttp.ClientSession() as session:
            # Ensure schema exists on Turso
            await _pipeline(session, [{"sql": s} for s in _SCHEMA_STMTS])

            # Wipe existing data in FK-safe order
            await _pipeline(session, [{"sql": f"DELETE FROM {t}"} for t in _DELETE_ORDER])

            # Insert rows in batches
            total = 0
            for table in _INSERT_ORDER:
                cols, rows = local_data[table]
                if not rows:
                    continue
                col_names = ",".join(cols)
                placeholders = ",".join(["?"] * len(cols))
                sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"
                for i in range(0, len(rows), _BATCH_SIZE):
                    batch = rows[i : i + _BATCH_SIZE]
                    stmts = [{"sql": sql, "args": [_arg(v) for v in row]} for row in batch]
                    results = await _pipeline(session, stmts)
                    errors = [r for r in results if r.get("type") == "error"]
                    if errors:
                        print(f"[turso] Insert error on {table}: {errors[0]}", flush=True)
                    total += len(batch)

        print(f"[turso] Backed up {total} rows.", flush=True)

    except Exception as exc:
        print(f"[turso] Backup failed: {exc}", flush=True)


async def run_periodic_backup(db_path: str, interval: int = 300) -> None:
    """Background coroutine: back up local DB to Turso every `interval` seconds."""
    print(f"[turso] Periodic backup started (every {interval}s).", flush=True)
    while True:
        await asyncio.sleep(interval)
        await backup_to_turso(db_path)
