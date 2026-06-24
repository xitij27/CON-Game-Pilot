# CommandPost

A Discord bot for managing competitive Conflict of Nations World War III games. Players with Corporal+ rank create matches through a central **Match Hub** channel, others register with a single button click, and the Map Leader locks a final roster — automatically making the channel private for selected players only.

## Features

- **Match Hub** — a dedicated `#match-hub` channel with a persistent control panel; the only entry point for creating matches
- **Fully button-driven** — no slash commands required; every action is accessible via buttons on pinned messages
- **Match creation wizard** — 5-step ephemeral UI: Game Type → Region → Timezone → Start Time → Confirm
- **Discord Scheduled Events** — created automatically with each match; editable by the leader after creation
- **Automatic channel creation** — named `{game-type}-{region}-{leadername}` under the `PREGAME` category
- **Player registration** — squad role + military role selects + country dropdowns with full validation; taken military roles are hidden from the dropdown
- **Re-registration** — players who withdraw can register again; their slot and country are freed immediately
- **Spy role** — no military role required; can pick from any country across the full game type map
- **Roster management** — leader toggles players in/out via an ephemeral panel, then locks; channel goes private instantly
- **Game start** — leader enters the 8-digit lobby code; channel is renamed to `{code}-{leadername}` and moved to the active games category
- **View Registrations** — any player can see the current roster (countries, roles) ephemerally at any time
- **Persistent buttons** — Register and Withdraw buttons survive bot restarts

## Setup

### 1. Create the bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**
2. Under **Bot**: enable **Server Members Intent**
3. Copy the bot token

### 2. Invite to your server

Use the OAuth2 URL Generator with scopes **`bot`** + **`applications.commands`** and the following permissions:

| Permission | Why |
|---|---|
| Manage Channels | Create and delete match channels |
| Manage Roles | — |
| Read Messages / View Channels | See channels and members |
| Send Messages | Post embeds and announcements |
| Embed Links | Rich embeds for roster and registration cards |
| Pin Messages | Pin the roster embed and game code message |
| Manage Messages | Unpin system messages after pinning |
| Manage Events | Create and edit Discord Scheduled Events |
| Read Message History | Fetch pinned messages after restart |

Set **Integration Type** to **Guild Install**.

### 3. Configure

```bash
cp .env.example .env
```

Fill in `.env`:

```env
DISCORD_BOT_TOKEN=your_token_here
SERVER_ID=your_guild_id_here

# Hub channel (optional — these are the defaults)
GAME_CENTRE_CATEGORY=game centre
MATCH_HUB_CHANNEL=match-hub
```

To get your Server ID: Discord **Settings → Advanced → enable Developer Mode**, then right-click your server icon → **Copy Server ID**.

### 4. Install and run

```bash
pip install -r requirements.txt
python bot.py
```

On first boot the bot will:
- Create the database (`commandpost.db`)
- Create the `game centre` category and `#match-hub` channel if they don't exist
- Post a fresh control panel in `#match-hub` and pin it

## Configuration

Settings in `config.py` (roles, categories) and `.env` (tokens, channel names):

| Setting | Where | Description |
|---|---|---|
| `DISCORD_BOT_TOKEN` | `.env` | Bot token from the Developer Portal |
| `SERVER_ID` | `.env` | Guild (server) ID |
| `GAME_CENTRE_CATEGORY` | `.env` | Category that contains the hub channel (default: `game centre`) |
| `MATCH_HUB_CHANNEL` | `.env` | Hub channel name (default: `match-hub`) |
| `ALLOWED_RANKS` | `config.py` | Role names that can create matches (Corporal and above) |
| `ADMIN_ROLES` | `config.py` | Roles that always keep channel access and can override leader actions |
| `PREGAME_CATEGORY` | `config.py` | Category where new match channels are created (default: `PREGAME`) |
| `VICTORY_CATEGORY` | `config.py` | Category channels move to after a win (default: `VICTORY WALL`) |
| `LOSS_CATEGORY` | `config.py` | Category channels move to after a loss (default: `LOSS LOG`) |
| `NEW_MAP_CHANNEL` | `config.py` | Channel for match announcements; skipped silently if absent |
| `MILITARY_ROLES` | `config.py` | One per player per match; list order = display order |
| `SQUAD_ROLE_LIMITS` | `config.py` | Per-role caps; `None` = unlimited |

Role names are **case-sensitive** and must exactly match your server's role names.

## Match flow

```
Leader clicks 🗺️ Create Match in #match-hub
  → wizard: Game Type → Region → Timezone → Start Time → Confirm
  → match channel created under PREGAME (named {game-type}-{region}-{leadername})
  → pinned roster embed posted with action buttons
  → Discord Scheduled Event created with the chosen start time

Players click 📋 Register on the pinned message
  → pick Squad Role + Military Role (taken roles are hidden)
  → pick Primary Country (and optionally Secondary)
  → registration card posted in channel; roster embed updates to remove claimed countries
  → click 🚪 Withdraw on their card to opt out

Leader clicks 🔒 Lock Roster
  → ephemeral panel: toggle players in/out → confirm lock
  → channel goes private (only selected players + admin roles)

Leader clicks 🎮 Start Game
  → enter the 8-digit lobby code
  → channel renamed to {code}-{leadername}, moved to active games category

(Optional) Leader clicks 📅 Edit Schedule
  → pick new timezone + date + time
  → Discord Scheduled Event updated in-place

Leader clicks ❌ Cancel Match
  → confirmation prompt
  → Discord event deleted, channel deleted after 5 seconds
```

## Button reference

### `#match-hub` control panel
| Button | Access | Action |
|---|---|---|
| 🗺️ Create Match | Corporal+ | Opens the match setup wizard |

### Pinned message — open match
| Button | Access | Action |
|---|---|---|
| 📋 Register | Anyone | Opens the registration flow |
| 🔒 Lock Roster | Leader / Admin | Opens the roster selection panel |
| 👥 Registrations | Anyone | Shows current registrations ephemerally |
| 📅 Edit Schedule | Leader / Admin | Updates the Discord event start time |
| ❌ Cancel Match | Leader only | Deletes the channel and event |

### Pinned message — locked match
| Button | Access | Action |
|---|---|---|
| 🔓 Unlock Roster | Leader | Reopens registration; resets all selections |
| 🎮 Start Game | Leader / Admin | Enter the 8-digit game code |
| 👥 Registrations | Anyone | Shows current registrations ephemerally |
| 📅 Edit Schedule | Leader / Admin | Updates the Discord event start time |
| ❌ Cancel Match | Leader only | Deletes the channel and event |

## Stack

- Python 3.12
- [py-cord](https://github.com/Pycord-Development/pycord) 2.7
- SQLite via [aiosqlite](https://github.com/omnilib/aiosqlite)
