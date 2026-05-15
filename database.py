import aiosqlite
from typing import Optional

DB_PATH = "strikebot.db"


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


# ── matches ──────────────────────────────────────────────────────────────────

async def create_match(
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


async def update_match_status(match_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE matches SET status = ? WHERE id = ?", (status, match_id))
        await db.commit()


# ── registrations ─────────────────────────────────────────────────────────────

async def create_registration(
    match_id: int, user_id: int,
    primary_country: str, secondary_country: Optional[str],
    military_role: str, squad_role: str,
) -> Optional[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            cur = await db.execute(
                "INSERT INTO registrations "
                "(match_id, user_id, primary_country, secondary_country, military_role, squad_role) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (match_id, user_id, primary_country, secondary_country, military_role, squad_role),
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
            "SELECT * FROM registrations WHERE status = 'pending'"
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


# ── convenience queries ───────────────────────────────────────────────────────

async def get_taken_countries(match_id: int) -> list[str]:
    regs = await get_registrations(match_id)
    taken = []
    for r in regs:
        taken.append(r["primary_country"].lower())
        if r["secondary_country"]:
            taken.append(r["secondary_country"].lower())
    return taken


async def get_taken_military_roles(match_id: int) -> list[str]:
    regs = await get_registrations(match_id)
    return [r["military_role"] for r in regs]


async def get_squad_role_counts(match_id: int) -> dict[str, int]:
    regs = await get_registrations(match_id)
    counts: dict[str, int] = {}
    for r in regs:
        counts[r["squad_role"]] = counts.get(r["squad_role"], 0) + 1
    return counts
