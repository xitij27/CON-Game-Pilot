# StrikeBot

A Discord bot for managing competitive map matches. Players with Corporal+ rank create matches, others register for them, and the Map Leader locks a final roster — which automatically makes the channel private for the selected players only.

## Features

- **Match creation wizard** — 3-step ephemeral UI: Game Type → Region → Confirm
- **Automatic channel generation** — channels are sorted under `1X GAMES`, `4X GAMES`, or `8X GAMES` categories
- **Player registration** — role selects + country modal with full validation (no duplicate countries or roles)
- **Spy role** — bypasses region lock and can pick from any country in the full game type map
- **Roster lock** — leader reviews registrations, toggles players in/out, locks the roster; channel permissions flip to private instantly
- **Persistent buttons** — Register and Withdraw buttons survive bot restarts

## Setup

### 1. Create the bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**
2. Under **Bot**: enable **Server Members Intent**
3. Copy the bot token

### 2. Invite to your server

Use the OAuth2 URL Generator with scopes `bot` + `applications.commands` and permissions:
`Manage Channels`, `Manage Roles`, `Send Messages`, `View Channels`, `Embed Links`, `Pin Messages`

Set **Integration Type** to **Guild Install**.

### 3. Configure

```bash
cp .env.example .env
```

Fill in `.env`:
```
DISCORD_BOT_TOKEN=your_token_here
SERVER_ID=your_guild_id_here
```

To get your Server ID: Discord **Settings → Advanced → enable Developer Mode**, then right-click your server icon → **Copy Server ID**.

### 4. Install and run

```bash
pip install -r requirements.txt
python bot.py
```

## Configuration

All tuneable settings are in `config.py`:

| Setting | Description |
|---|---|
| `ALLOWED_RANKS` | Role names that can run `/creatematch` (Corporal and above) |
| `ADMIN_ROLES` | Role names that always keep channel access + can override |
| `SCALE_CATEGORIES` | Discord category names for `1X`, `4X`, `8X` match tiers |
| `GAME_TYPE_SCALE` | Maps each game type to its tier |
| `NEW_MAP_CHANNEL` | Channel name for match announcements |

Role names are **case-sensitive** and must exactly match your server's role names.

## Commands

| Command | Access | Description |
|---|---|---|
| `/creatematch` | Corporal+ | Launch the match setup wizard |
| `/roster` | Map Leader / Admin | Open the roster management panel |
| `/cancelmatch` | Map Leader / Admin | Cancel the match and delete the channel |
| `/withdraw` | Registered players | Withdraw your registration before the roster locks |

## Stack

- Python 3.12
- [py-cord](https://github.com/Pycord-Development/pycord) 2.7
- SQLite via [aiosqlite](https://github.com/omnilib/aiosqlite)
