# CommandPost — Codebase Guide

## What this is

A Discord bot for managing competitive map matches on a single guild. Players with Corporal+ rank can create matches, other players register for them, and the Map Leader locks a final roster which makes the channel private. Built with **Python 3.12**, **py-cord 2.4+**, and **SQLite via aiosqlite**.

## Running the bot

```bash
pip install -r requirements.txt
# fill in .env (copy .env.example)
python bot.py
```

Environment variables (`DISCORD_BOT_TOKEN`, `SERVER_ID`) are loaded from `.env` via python-dotenv. The SQLite database (`commandpost.db`) is created automatically on first boot.

---

## File map

```
bot.py                  Entry point. Defines CommandPost, restores persistent views on startup.
config.py               All tuneable constants: rank names, admin roles, category names, role limits.
database.py             Every DB read/write. All functions are async (aiosqlite). No ORM.
data/
  game_data.py          Static game data dict + pure helper functions. No I/O, no Discord imports.
cogs/
  match.py              All slash commands: /creategame /startgame /help /roster /cancelgame /withdraw.
views/
  setup_views.py        SetupWizard — ephemeral 3-step match configuration UI.
  register_view.py      Full registration flow: Register button → role selects → country select/modal → card.
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
/creategame
  → SetupWizard (ephemeral, 3 steps: game type → region → confirm)
  → _wizard_confirmed callback in MatchCog
      → creates Discord channel under PREGAME category
      → inserts row into matches table (status='open')
      → posts roster embed + pins it with RegisterMatchView (persistent)
      → announces in #new-map-chat if the channel exists

Player clicks Register
  → _RegisterButton.callback (persistent, survives restarts)
  → _RoleSelectionView (ephemeral, role selects + Continue button)
      → non-Spy: _CountrySelectView (two dropdowns: primary + secondary + Register button)
      → Spy: _CountryModal (text inputs — searches all regions)
  → validate → upsert registrations row → post registration card

/roster  (leader or admin)
  → RosterPanel (ephemeral, toggle buttons per player + Lock Roster)
  → _LockConfirmView → locks match in DB (status='locked') → edits channel overwrites → posts summary

/startgame <8-digit-code>  (leader or admin, roster must be locked)
  → validates code is exactly 8 digits
  → sets game_code in DB (status='started')
  → renames channel to {code}-{leadername}
  → posts Game Found embed

/cancelgame or /withdraw  → DB update → optional channel deletion
```

### Persistent views

Discord buttons stop working when the bot restarts unless the view is re-registered. Two view types are persistent (`timeout=None` + explicit `custom_id`):

| View | custom_id pattern | Restored in |
|---|---|---|
| `RegisterMatchView` | `register_match_{channel_id}` | `bot.setup_hook` queries all open matches |
| `RegistrationCardView` | `withdraw_reg_{reg_id}` | `bot.setup_hook` queries `status='pending'` registrations |

Non-persistent views (`SetupWizard`, `_RoleSelectionView`, `_CountrySelectView`, `RosterPanel`) are ephemeral and short-lived — losing them on restart is acceptable.

### Registration multi-step state

The registration flow has two distinct paths after role selection:

**Non-Spy players** go to `_CountrySelectView` — two dropdowns (primary + secondary) built from the currently-available countries at the moment the player clicks Continue. Both dropdowns exclude any country already taken as someone's primary.

**Spy players** go to `_CountryModal` (text inputs) because Spy searches across all regions and there are too many options for a dropdown.

In both paths, `_RoleSelectionView` stores partial state in the module-level `_pending: dict[int, dict]` keyed by `user_id` only for the Spy path (needed because modals can't carry context). Non-Spy players flow directly to `_CountrySelectView` without using `_pending`.

`_pending` is in-memory only. If the bot restarts between a Spy player clicking Continue and submitting the modal, the modal submit will find nothing in `_pending` and tell the player to start over.

### Country availability rules

- **Primary country** — must be unique per match. Taken primaries are excluded from both the primary and secondary dropdowns (and from free-text modal validation for Spy).
- **Secondary country** — may not be a country already taken as someone else's primary (same exclusion list). May not be the same as the player's own primary.
- Secondary overlapping with another player's secondary is allowed.
- Race-condition re-checks at submit time enforce both rules even if the dropdowns were stale.

### Leader squad role enforcement

`leader_id` in the DB (the person who ran `/creategame`) and the "Leader" **squad role** are two separate concepts. Enforcement:

- The Register button detects `is_leader = interaction.user.id == match["leader_id"]` and forces squad_role="Leader" for the leader, hiding the dropdown.
- Non-leader players' squad dropdown explicitly excludes "Leader" (`r != "Leader"` filter).
- Server-side guard in `_on_continue` rejects any non-leader who somehow arrives with squad_role="Leader".
- The Leader squad slot is exempt from the "slot full" check — the leader's slot is always reserved for them and can't be blocked by anyone else.

### Re-registration after withdrawal

The `registrations` table has `UNIQUE(match_id, user_id)`. Withdrawal sets `status='withdrawn'` but keeps the row. If a withdrawn player tries to register again, `create_registration()` detects the withdrawn row and **UPDATE**s it (new country/role, status reset to 'pending', message_id cleared) instead of INSERT. This avoids the IntegrityError that would otherwise produce a false "already registered" error.

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
| `ALLOWED_RANKS` | Role names that can run `/creategame`. Order matters: `[0]` is used in the error message. |
| `ADMIN_ROLES` | Role names that always keep channel access after lock and can run `/roster`. |
| `PREGAME_CATEGORY` | Discord category name where match channels are created. Default: `"PREGAME"`. |
| `SCALE_CATEGORIES` | Defined but **not currently used** for channel creation. Reserved for future scale-based sorting. |
| `GAME_TYPE_SCALE` | Defined but **not currently used** for channel creation. Maps game type → scale tier. |
| `NEW_MAP_CHANNEL` | Channel name where match announcements are posted. Bot skips silently if it doesn't exist. |
| `MILITARY_ROLES` | Exactly one of each per match. List order = display order. |
| `SQUAD_ROLE_LIMITS` | `None` = unlimited. Change `"Spy": 1` to `"Spy": 2` etc. if rules change. |

Role name matching is **exact and case-sensitive** against `discord.Member.roles[n].name`.

---

## Database schema

**`matches`**
```
id, channel_id (UNIQUE), guild_id, leader_id, game_type, region,
game_code (nullable — set by /startgame), roster_message_id (nullable — the pinned embed),
status ('open' | 'locked' | 'started' | 'cancelled'), created_at
```
Note: a `speed` column existed in early versions and is dropped on boot if present (`init_db` handles migration).

**`registrations`**
```
id, match_id (FK), user_id, primary_country, secondary_country (nullable),
military_role, squad_role, message_id (nullable — the card message),
status ('pending' | 'selected' | 'rejected' | 'withdrawn'), registered_at
UNIQUE(match_id, user_id)
```

`get_registrations()` and all availability queries filter out `status = 'withdrawn'` rows, so withdrawn players free up their countries and roles immediately. The UNIQUE constraint is handled by `create_registration()` detecting and updating withdrawn rows rather than inserting.

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

All commands are guild-scoped (`debug_guilds=[config.GUILD_ID]`) for instant registration. Switching to global commands requires removing that parameter and waiting up to an hour for Discord to propagate.

| Command | Who can use it | Where |
|---|---|---|
| `/creategame` | Any member with a role in `ALLOWED_RANKS` | Anywhere |
| `/startgame <code>` | Match leader or `ADMIN_ROLES` | Inside the match channel (roster must be locked) |
| `/help` | Anyone | Anywhere |
| `/roster` | Match leader or `ADMIN_ROLES` | Inside the match channel |
| `/cancelgame` | Match leader or `ADMIN_ROLES` | Inside the match channel |
| `/withdraw` | Any registered player | Inside the match channel |

---

## Known gotchas

- **`discord.Option` syntax** — must be used as a default value, not a type annotation. Correct: `code: str = discord.Option(description="...")`. Using it as `code: discord.Option(str, "...")` causes `TypeError: issubclass() arg 1 must be a class` at invocation time in py-cord 2.x.
- **`SCALE_CATEGORIES` / `GAME_TYPE_SCALE`** — present in `config.py` but channel creation uses only `PREGAME_CATEGORY`. These are vestigial from an earlier design and can be wired up later if scale-based category sorting is needed.
- **Leader vs. Leader squad role** — `match.leader_id` (the `/creategame` caller) and the "Leader" squad role are independent. A player can have the Leader squad role only if their user ID matches `leader_id`. Both are enforced at the UI level (dropdown filter) and server-side (guard in `_on_continue`).
- **Registration card restore on restart** — only `status='pending'` cards get their Withdraw button restored. `selected` / `rejected` cards lose the button but those states don't normally need withdrawing.
- **Roster message sync** — `_update_roster_embed` edits the pinned embed to remove claimed countries after each registration or withdrawal. It silently no-ops if `roster_message_id` is missing or the message was deleted.
