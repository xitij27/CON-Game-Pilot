# StrikeBot — Codebase Guide

## What this is

A Discord bot for managing competitive map matches on a single guild. Players with Corporal+ rank can create matches, other players register for them, and the Map Leader locks a final roster which makes the channel private. Built with **Python 3.12**, **py-cord 2.4+**, and **SQLite via aiosqlite**.

## Running the bot

```bash
pip install -r requirements.txt
# fill in .env (copy .env.example)
python bot.py
```

Environment variables (`DISCORD_BOT_TOKEN`, `SERVER_ID`) are loaded from `.env` via python-dotenv. The SQLite database (`strikebot.db`) is created automatically on first boot.

---

## File map

```
bot.py                  Entry point. Defines StrikeBot, restores persistent views on startup.
config.py               All tuneable constants: rank names, admin roles, category names, role limits.
database.py             Every DB read/write. All functions are async (aiosqlite). No ORM.
data/
  game_data.py          Static game data dict + pure helper functions. No I/O, no Discord imports.
cogs/
  match.py              The four slash commands: /creatematch /roster /cancelmatch /withdraw.
views/
  setup_views.py        SetupWizard — ephemeral 4-step match configuration UI.
  register_view.py      Full registration flow: Register button → role selects → country modal → card.
  roster_view.py        RosterPanel — leader-only toggle+lock UI.
```

---

## Architecture

### Layer separation

```
Discord interactions
      ↓
  cogs/match.py          (commands — entry points, permission checks, orchestration)
      ↓
  views/*.py             (UI components — Views, Modals, Buttons; call DB directly)
      ↓
  database.py            (all SQL — called from both cogs and views)
      ↓
  data/game_data.py      (pure data — called from views and cogs, never touches DB)
```

`config.py` is imported by every layer. `data/game_data.py` has no side-effects and is safe to import anywhere.

### Match lifecycle

```
/creatematch
  → SetupWizard (ephemeral, 4 steps: game type → speed → region → confirm)
  → _wizard_confirmed callback in MatchCog
      → creates Discord channel under the correct scale category (1X/4X/8X GAMES)
      → inserts row into matches table
      → posts roster embed + pins it with RegisterMatchView (persistent)
      → announces in #new-map-chat if the channel exists

Player clicks Register
  → _RegisterButton.callback (persistent, survives restarts)
  → _RoleSelectionView (ephemeral, 2 selects + Continue button)
  → _CountryModal (Discord modal, 2 text inputs)
  → on_submit: validate → insert registrations row → post registration card

/roster  (leader or admin)
  → RosterPanel (ephemeral, toggle buttons per player + Lock Roster)
  → _LockConfirmView → locks match in DB → edits channel overwrites → posts summary

/cancelmatch or /withdraw  → DB update → optional channel deletion
```

### Persistent views

Discord buttons stop working when the bot restarts unless the view is re-registered. Two view types are persistent (`timeout=None` + explicit `custom_id`):

| View | custom_id pattern | Restored in |
|---|---|---|
| `RegisterMatchView` | `register_match_{channel_id}` | `bot.setup_hook` queries all open matches |
| `RegistrationCardView` | `withdraw_reg_{reg_id}` | `bot.setup_hook` queries all pending registrations |

Non-persistent views (`SetupWizard`, `_RoleSelectionView`, `RosterPanel`) are ephemeral and short-lived — losing them on restart is acceptable.

### Registration two-step state

Discord modals can only contain text inputs, so country selection can't be a dropdown inside a modal. The solution: `_RoleSelectionView` collects squad + military role via selects, then on Continue stores partial state in the module-level `_pending: dict[int, dict]` keyed by `user_id`, then opens `_CountryModal`. `on_submit` pops from `_pending` to reunite the two halves before writing to DB.

`_pending` is in-memory only. If the bot restarts between the user clicking Continue and submitting the modal, the modal submit will find nothing in `_pending` and tell the user to start over.

### Spy role country bypass

Standard registrations call `find_country_in_region(game_type, region, name)` which only looks within the match's region. When `squad_role == "Spy"`, the code calls `find_country(game_type, name)` instead, which searches all regions via `get_all_countries()`. The country validation, availability check, and race-condition re-check all run the same way regardless — only the lookup pool differs.

### Channel permissions on lock

When the leader locks a roster, `_LockConfirmView._confirm` builds a `permission_overwrites` dict from scratch:
1. `@everyone` → `read_messages=False`
2. Any role whose name is in `config.ADMIN_ROLES` → full access
3. The match leader (by stored `leader_id`) → full access
4. Each selected player (by `user_id`) → full access

Then calls `channel.edit(overwrites=overwrites)`. Anyone not in that dict loses access automatically.

---

## Configuration reference (`config.py`)

| Constant | What it controls |
|---|---|
| `ALLOWED_RANKS` | Role names that can run `/creatematch`. Order matters: `[0]` is used in the error message. |
| `ADMIN_ROLES` | Role names that always keep channel access after lock and can run `/roster`. |
| `SCALE_CATEGORIES` | Maps `"1X"/"4X"/"8X"` → Discord category name. Change these strings to rename the categories. |
| `GAME_TYPE_SCALE` | Maps each game type string → its scale tier. Fill in the `# TODO` entries as you add data. |
| `NEW_MAP_CHANNEL` | Channel name where match announcements are posted. Bot skips silently if it doesn't exist. |
| `MILITARY_ROLES` | Exactly one of each per match. List order = display order. |
| `SQUAD_ROLE_LIMITS` | `None` = unlimited. Change `"Spy": 1` to `"Spy": 2` etc. if rules change. |

Role name matching is **exact and case-sensitive** against `discord.Member.roles[n].name`.

---

## Database schema

**`matches`**
```
id, channel_id (UNIQUE), guild_id, leader_id, game_type, speed, region,
status ('open' | 'locked' | 'cancelled'), created_at
```

**`registrations`**
```
id, match_id (FK), user_id, primary_country, secondary_country (nullable),
military_role, squad_role, message_id (nullable — the card message),
status ('pending' | 'selected' | 'rejected' | 'withdrawn'), registered_at
```

`get_registrations()` and all availability queries filter out `status = 'withdrawn'` rows, so withdrawn players free up their countries and roles immediately.

---

## Game data (`data/game_data.py`)

`GAME_DATA` is a plain dict:
```python
GAME_DATA = {
    "WW3 4X": {
        "speeds": ["Normal", "Apocalypse"],
        "regions": {
            "America": [
                {"name": "USA", "doctrine": "Western", "cities": 9},
                ...
            ],
        },
    },
    "WW3 1X": {"speeds": ["Normal"], "regions": {}},  # placeholder
    ...
}
```

**To add a new game type's data:** populate its `"regions"` dict with the same structure. The wizard, roster embed, country validation, and Spy lookup all derive from this dict — no other code changes needed.

Helper functions (`get_regions`, `get_countries`, `get_all_countries`, `find_country`, `find_country_in_region`) are pure — no I/O, no Discord state. Safe to call anywhere including sync contexts.

Only **WW3 4X** has full data (9 regions, 63 unique countries). All other game types are stubs with empty `regions` dicts. The wizard warns the leader if they pick a game type with no region data but still allows proceeding.

---

## Slash commands

All commands are guild-scoped (`guild_ids=[config.GUILD_ID]`) for instant registration. Switching to global commands requires removing that parameter and waiting up to an hour for Discord to propagate.

| Command | Who can use it | Where |
|---|---|---|
| `/creatematch` | Any member with a role in `ALLOWED_RANKS` | Anywhere |
| `/roster` | Match leader or `ADMIN_ROLES` | Inside the match channel |
| `/cancelmatch` | Match leader or `ADMIN_ROLES` | Inside the match channel |
| `/withdraw` | Any registered player | Inside the match channel |
