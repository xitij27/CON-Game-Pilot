# CommandPost

A Discord bot for managing competitive Conflict of Nations World War III games. Players with Corporal+ rank create matches, others register for them, and the Map Leader locks a final roster — which automatically makes the channel private for the selected players only.

## Features

- **Match creation wizard** — 3-step ephemeral UI: Game Type → Region → Confirm
- **Automatic channel creation** — channels are created under the `PREGAME` category (configurable)
- **Player registration** — role selects + country dropdowns with full validation (no duplicate primary countries or roles)
- **Re-registration** — players who withdraw can register again with new choices; their slot is freed immediately on withdrawal
- **Spy role** — bypasses region lock; can pick from any country in the full game type map
- **Roster lock** — leader reviews registrations, toggles players in/out, locks the roster; channel permissions flip to private instantly
- **Game code** — leader enters the 8-digit lobby code via `/startgame`; channel is renamed to `{code}-{leadername}`
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
| `ALLOWED_RANKS` | Role names that can run `/creategame` (Corporal and above) |
| `ADMIN_ROLES` | Role names that always keep channel access + can override leader commands |
| `PREGAME_CATEGORY` | Discord category name where match channels are created |
| `NEW_MAP_CHANNEL` | Channel name for match announcements (skipped silently if absent) |
| `MILITARY_ROLES` | One per player per match; list order = display order |
| `SQUAD_ROLE_LIMITS` | Per-role caps; `None` = unlimited |

Role names are **case-sensitive** and must exactly match your server's role names.

## Commands

| Command | Access | Description |
|---|---|---|
| `/creategame` | Corporal+ | Launch the match setup wizard |
| `/startgame <code>` | Map Leader / Admin | Enter the 8-digit game lobby code once found; renames the channel |
| `/help` | Anyone | Show all available commands |
| `/roster` | Map Leader / Admin | Open the roster panel to select players and lock the roster |
| `/cancelgame` | Map Leader / Admin | Cancel the match and delete the channel |
| `/withdraw` | Registered players | Withdraw your registration before the roster locks |

## Match flow

```
Leader runs /creategame
  → picks game type + region
  → channel created under PREGAME, pinned roster embed posted

Players click Register in the channel
  → pick military role + squad role
  → pick primary country (and optionally secondary)
  → registration card posted; roster embed updates to remove claimed countries

Leader runs /roster
  → toggles players in/out → locks roster
  → channel goes private (only selected players + admins)

Leader runs /startgame <8-digit code>
  → channel renamed to {code}-{leadername}
  → game code announced in channel
```

## Stack

- Python 3.12
- [py-cord](https://github.com/Pycord-Development/pycord) 2.7
- SQLite via [aiosqlite](https://github.com/omnilib/aiosqlite)
