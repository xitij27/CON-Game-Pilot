import aiosqlite
from typing import Optional

DB_PATH = "commandpost.db"


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id  INTEGER UNIQUE,
                guild_id    INTEGER NOT NULL,
                leader_id   INTEGER NOT NULL,
                game_type   TEXT NOT NULL,
                region      TEXT NOT NULL,
                status      TEXT DEFAULT 'open',
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS registrations (
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
            )
        """)
        await db.commit()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("PRAGMA table_info(matches)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        if "speed" in cols:
            await db.execute("ALTER TABLE matches DROP COLUMN speed")
            await db.commit()
        if "game_code" not in cols:
            await db.execute("ALTER TABLE matches ADD COLUMN game_code TEXT")
            await db.commit()
        if "roster_message_id" not in cols:
            await db.execute("ALTER TABLE matches ADD COLUMN roster_message_id INTEGER")
            await db.commit()
        if "hub_message_id" not in cols:
            await db.execute("ALTER TABLE matches ADD COLUMN hub_message_id INTEGER")
            await db.commit()
        if "event_id" not in cols:
            await db.execute("ALTER TABLE matches ADD COLUMN event_id INTEGER")
            await db.commit()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await db.commit()


# ── matches ──────────────────────────────────────────────────────────────────

async def create_game(
    channel_id: int, guild_id: int, leader_id: int,
    game_type: str, region: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO matches (channel_id, guild_id, leader_id, game_type, region) "
            "VALUES (?, ?, ?, ?, ?)",
            (channel_id, guild_id, leader_id, game_type, region),
        )
        await db.commit()
        return cur.lastrowid


async def get_match_by_channel(channel_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM matches WHERE channel_id = ?", (channel_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_open_match_by_leader(leader_id: int, guild_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM matches WHERE leader_id = ? AND guild_id = ? AND status = 'open'",
            (leader_id, guild_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_open_matches() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM matches WHERE status = 'open'") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_non_cancelled_matches() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM matches WHERE status != 'cancelled'") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def update_match_status(match_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE matches SET status = ? WHERE id = ?", (status, match_id))
        await db.commit()


async def set_roster_message_id(match_id: int, message_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE matches SET roster_message_id = ? WHERE id = ?",
            (message_id, match_id),
        )
        await db.commit()


async def set_game_code(match_id: int, code: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE matches SET game_code = ?, status = 'started' WHERE id = ?",
            (code, match_id),
        )
        await db.commit()


# ── registrations ─────────────────────────────────────────────────────────────

async def create_registration(
    match_id: int, user_id: int,
    primary_country: str, secondary_country: Optional[str],
    military_role: Optional[str], squad_role: str,
) -> Optional[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        # Re-registration after withdrawal: update the existing row rather than insert
        async with db.execute(
            "SELECT id FROM registrations WHERE match_id = ? AND user_id = ? AND status = 'withdrawn'",
            (match_id, user_id),
        ) as cur:
            withdrawn = await cur.fetchone()

        mil = military_role or ""  # Spy has no military role; store "" to satisfy NOT NULL

        if withdrawn:
            reg_id = withdrawn[0]
            await db.execute(
                "UPDATE registrations "
                "SET primary_country = ?, secondary_country = ?, military_role = ?, "
                "    squad_role = ?, status = 'pending', message_id = NULL "
                "WHERE id = ?",
                (primary_country, secondary_country, mil, squad_role, reg_id),
            )
            await db.commit()
            return reg_id

        try:
            cur = await db.execute(
                "INSERT INTO registrations "
                "(match_id, user_id, primary_country, secondary_country, military_role, squad_role) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (match_id, user_id, primary_country, secondary_country, mil, squad_role),
            )
            await db.commit()
            return cur.lastrowid
        except aiosqlite.IntegrityError:
            return None


async def get_registration(match_id: int, user_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM registrations WHERE match_id = ? AND user_id = ? AND status != 'withdrawn'",
            (match_id, user_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_registrations(match_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM registrations WHERE match_id = ? AND status != 'withdrawn'",
            (match_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_all_active_registrations() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM registrations WHERE status != 'withdrawn'"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def update_registration_message(reg_id: int, message_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE registrations SET message_id = ? WHERE id = ?", (message_id, reg_id)
        )
        await db.commit()


async def update_registration_status(reg_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE registrations SET status = ? WHERE id = ?", (status, reg_id)
        )
        await db.commit()


async def withdraw_registration(reg_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE registrations SET status = 'withdrawn' WHERE id = ?", (reg_id,)
        )
        await db.commit()


async def reopen_match_registrations(match_id: int) -> None:
    """Reset selected/rejected registrations to pending when a roster is unlocked."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE registrations SET status = 'pending' "
            "WHERE match_id = ? AND status IN ('selected', 'rejected')",
            (match_id,),
        )
        await db.commit()


# ── convenience queries ───────────────────────────────────────────────────────

async def get_taken_primary_countries(match_id: int) -> list[str]:
    regs = await get_registrations(match_id)
    return [r["primary_country"].lower() for r in regs]


async def get_taken_military_roles(match_id: int) -> list[str]:
    regs = await get_registrations(match_id)
    return [r["military_role"] for r in regs if r["military_role"]]


async def get_squad_role_counts(match_id: int) -> dict[str, int]:
    regs = await get_registrations(match_id)
    counts: dict[str, int] = {}
    for r in regs:
        counts[r["squad_role"]] = counts.get(r["squad_role"], 0) + 1
    return counts


async def set_event_id(match_id: int, event_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE matches SET event_id = ? WHERE id = ?",
            (event_id, match_id),
        )
        await db.commit()


async def set_hub_message_id(match_id: int, message_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE matches SET hub_message_id = ? WHERE id = ?",
            (message_id, match_id),
        )
        await db.commit()


# ── settings (key-value store) ────────────────────────────────────────────────

async def get_setting(key: str) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()
