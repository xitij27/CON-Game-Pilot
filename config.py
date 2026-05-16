import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN: str = os.getenv("DISCORD_BOT_TOKEN", "")
GUILD_ID: int = int(os.getenv("SERVER_ID", "0"))

# Roles permitted to run /creategame (Corporal and above)
ALLOWED_RANKS: list[str] = [
    "Corporal",
    "Sergeant",
    "Lieutenant",
    "Captain",
    "Major",
    "Colonel",
    "General",
]

# Roles that always keep channel access after roster lock
ADMIN_ROLES: list[str] = [
    "Admin",
    "Map Master",
    "Moderator",
    "Owner",
]

PREGAME_CATEGORY: str = "PREGAME"
VICTORY_CATEGORY: str = "VICTORY WALL"
LOSS_CATEGORY:    str = "LOSS LOG"

# Discord category names for each scale tier
SCALE_CATEGORIES: dict[str, str] = {
    "1X": "1X GAMES",
    "4X": "4X GAMES",
    "8X": "8X GAMES",
}

# Map every game type to its scale tier.
# WW3 entries are set; fill in the rest as you add their data.
GAME_TYPE_SCALE: dict[str, str] = {
    "WW3 1X":               "1X",
    "WW3 4X":               "4X",
    "Flashpoint":           "1X",   # TODO: confirm scale
    "Battleground USA":     "1X",   # TODO
    "Overkill":             "4X",   # TODO
    "Rising Tides":         "1X",   # TODO
    "Civil War America":    "1X",   # TODO
    "Pacific Theater":      "4X",   # TODO
    "Rising Sun Apocalypse":"8X",   # TODO
    "Nuclear Winter":       "8X",   # TODO
    "Blood and Oil":        "1X",   # TODO
    "Middle East Conflict": "1X",   # TODO
}

NEW_MAP_CHANNEL: str = "new-map-chat"

MILITARY_ROLES: list[str] = [
    "Ground Support",
    "Strike Group",
    "Task Force",
    "Aerial Command",
    "Combat Wing",
]

SQUAD_ROLES: list[str] = ["Leader", "Scout", "Spy", "Soldier"]

# None = unlimited
SQUAD_ROLE_LIMITS: dict[str, int | None] = {
    "Leader": 1,
    "Scout": 1,
    "Spy": 1,
    "Soldier": None,
}
