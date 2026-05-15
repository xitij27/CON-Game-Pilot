"""Registration flow: persistent Register button → role selects → country modal → card."""
from __future__ import annotations

from typing import Optional
import discord

import config
import database as db
from data.game_data import get_countries, get_all_countries, find_country, find_country_in_region

# Ephemeral state keyed by user_id while they fill in the country modal
_pending: dict[int, dict] = {}

DOCTRINE_COLORS = {
    "Western":  discord.Color.blue(),
    "Eastern":  discord.Color.red(),
    "European": discord.Color.gold(),
}


# ── Persistent outer view (lives on the pinned roster message) ────────────────

class RegisterMatchView(discord.ui.View):
    """Added to the bot and re-added on every restart for each open match."""

    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        self.add_item(_RegisterButton(channel_id))


class _RegisterButton(discord.ui.Button):
    def __init__(self, channel_id: int):
        super().__init__(
            label="Register",
            style=discord.ButtonStyle.primary,
            emoji="📋",
            custom_id=f"register_match_{channel_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        match = await db.get_match_by_channel(interaction.channel_id)
        if not match:
            await interaction.response.send_message("This match no longer exists.", ephemeral=True)
            return
        if match["status"] != "open":
            await interaction.response.send_message("This match's roster is locked.", ephemeral=True)
            return

        existing = await db.get_registration(match["id"], interaction.user.id)
        if existing:
            await interaction.response.send_message(
                "You're already registered. Use `/withdraw` or the **Withdraw** button on your card to opt out.",
                ephemeral=True,
            )
            return

        view = _RoleSelectionView(match)
        embed = discord.Embed(
            title="Registration — Step 1 of 2",
            description=(
                f"**{match['game_type']} · {match['region']}**\n\n"
                "Pick your **Military Role** and **Squad Role**, then click **Continue**.\n"
                "-# Spy role unlocks all countries across the full game type map."
            ),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ── Step 1: role selects ──────────────────────────────────────────────────────

class _RoleSelectionView(discord.ui.View):
    def __init__(self, match: dict):
        super().__init__(timeout=120)
        self.match = match
        self.squad_role: Optional[str] = None
        self.military_role: Optional[str] = None

        squad_select = discord.ui.Select(
            placeholder="Squad Role...",
            options=[
                discord.SelectOption(label=r, value=r, description=_squad_desc(r))
                for r in config.SQUAD_ROLES
            ],
            custom_id="squad_role_select",
        )
        squad_select.callback = self._on_squad_role
        self.add_item(squad_select)

        mil_select = discord.ui.Select(
            placeholder="Military Role...",
            options=[discord.SelectOption(label=r, value=r) for r in config.MILITARY_ROLES],
            custom_id="military_role_select",
        )
        mil_select.callback = self._on_military_role
        self.add_item(mil_select)

        continue_btn = discord.ui.Button(
            label="Continue →",
            style=discord.ButtonStyle.primary,
            custom_id="reg_continue",
        )
        continue_btn.callback = self._on_continue
        self.add_item(continue_btn)

    async def _on_squad_role(self, interaction: discord.Interaction) -> None:
        self.squad_role = interaction.data["values"][0]
        await interaction.response.defer()

    async def _on_military_role(self, interaction: discord.Interaction) -> None:
        self.military_role = interaction.data["values"][0]
        await interaction.response.defer()

    async def _on_continue(self, interaction: discord.Interaction) -> None:
        if not self.squad_role or not self.military_role:
            await interaction.response.send_message(
                "Please select both a Squad Role and a Military Role first.", ephemeral=True
            )
            return

        match_id = self.match["id"]

        # Validate squad role slot
        sq_counts = await db.get_squad_role_counts(match_id)
        limit = config.SQUAD_ROLE_LIMITS.get(self.squad_role)
        if limit is not None and sq_counts.get(self.squad_role, 0) >= limit:
            await interaction.response.send_message(
                f"The **{self.squad_role}** slot is already filled. Choose a different Squad Role.",
                ephemeral=True,
            )
            return

        # Validate military role slot
        taken_mil = await db.get_taken_military_roles(match_id)
        if self.military_role in taken_mil:
            free = ", ".join(r for r in config.MILITARY_ROLES if r not in taken_mil)
            await interaction.response.send_message(
                f"**{self.military_role}** is taken. Available: {free or 'none'}",
                ephemeral=True,
            )
            return

        # Stash partial state and open the country modal
        _pending[interaction.user.id] = {
            "match_id":     match_id,
            "game_type":    self.match["game_type"],
            "region":       self.match["region"],
            "squad_role":   self.squad_role,
            "military_role": self.military_role,
        }
        await interaction.response.send_modal(_CountryModal(self.match))


# ── Step 2: country name modal ────────────────────────────────────────────────

class _CountryModal(discord.ui.Modal):
    def __init__(self, match: dict):
        super().__init__(title="Country Selection")
        countries = get_countries(match["game_type"], match["region"])
        hint_primary = countries[0]["name"] if countries else "Country name"
        hint_secondary = countries[1]["name"] if len(countries) > 1 else "Country name or leave blank"

        self.primary = discord.ui.InputText(
            label="Primary Country",
            placeholder=hint_primary,
            style=discord.InputTextStyle.short,
        )
        self.secondary = discord.ui.InputText(
            label="Secondary Country (optional)",
            placeholder=hint_secondary,
            style=discord.InputTextStyle.short,
            required=False,
        )
        self.add_item(self.primary)
        self.add_item(self.secondary)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        pending = _pending.pop(interaction.user.id, None)
        if not pending:
            await interaction.response.send_message(
                "Session expired — please click Register again.", ephemeral=True
            )
            return

        primary_name  = self.primary.value.strip()
        secondary_name = self.secondary.value.strip() if self.secondary.value else None
        squad_role    = pending["squad_role"]
        military_role = pending["military_role"]
        match_id      = pending["match_id"]
        game_type     = pending["game_type"]
        region        = pending["region"]
        is_spy        = squad_role == "Spy"

        # Resolve primary country
        if is_spy:
            primary_c = find_country(game_type, primary_name)
        else:
            primary_c = find_country_in_region(game_type, region, primary_name)

        if not primary_c:
            pool = get_all_countries(game_type) if is_spy else get_countries(game_type, region)
            names = ", ".join(c["name"] for c in pool)
            await interaction.response.send_message(
                f"**{primary_name}** isn't a valid country for this match.\nAvailable: {names}",
                ephemeral=True,
            )
            return

        # Resolve secondary country
        secondary_c: Optional[dict] = None
        if secondary_name:
            if is_spy:
                secondary_c = find_country(game_type, secondary_name)
            else:
                secondary_c = find_country_in_region(game_type, region, secondary_name)

            if not secondary_c:
                await interaction.response.send_message(
                    f"**{secondary_name}** isn't valid for this match.", ephemeral=True
                )
                return
            if secondary_c["name"].lower() == primary_c["name"].lower():
                await interaction.response.send_message(
                    "Primary and Secondary country can't be the same.", ephemeral=True
                )
                return

        # Check availability
        taken = await db.get_taken_countries(match_id)
        if primary_c["name"].lower() in taken:
            await interaction.response.send_message(
                f"**{primary_c['name']}** is already claimed by another player.", ephemeral=True
            )
            return
        if secondary_c and secondary_c["name"].lower() in taken:
            await interaction.response.send_message(
                f"**{secondary_c['name']}** is already claimed by another player.", ephemeral=True
            )
            return

        # Race-condition re-check on roles
        sq_counts = await db.get_squad_role_counts(match_id)
        limit = config.SQUAD_ROLE_LIMITS.get(squad_role)
        if limit is not None and sq_counts.get(squad_role, 0) >= limit:
            await interaction.response.send_message(
                f"**{squad_role}** was just taken. Please restart registration.", ephemeral=True
            )
            return

        taken_mil = await db.get_taken_military_roles(match_id)
        if military_role in taken_mil:
            await interaction.response.send_message(
                f"**{military_role}** was just taken. Please restart registration.", ephemeral=True
            )
            return

        # Commit registration
        reg_id = await db.create_registration(
            match_id, interaction.user.id,
            primary_c["name"],
            secondary_c["name"] if secondary_c else None,
            military_role, squad_role,
        )
        if reg_id is None:
            await interaction.response.send_message("You're already registered.", ephemeral=True)
            return

        # Post card to channel (visible to everyone)
        card_embed = _build_card(interaction.user, primary_c, secondary_c, military_role, squad_role)
        card_view = RegistrationCardView(reg_id)
        await interaction.response.send_message(embed=card_embed, view=card_view)

        msg = await interaction.original_response()
        await db.update_registration_message(reg_id, msg.id)


# ── Registration card + withdraw button ───────────────────────────────────────

def _build_card(
    user: discord.Member,
    primary: dict,
    secondary: Optional[dict],
    military_role: str,
    squad_role: str,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"✅  {user.display_name}  —  Registered",
        color=DOCTRINE_COLORS.get(primary["doctrine"], discord.Color.greyple()),
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(
        name="🎯 Primary",
        value=f"**{primary['name']}**\n{primary['doctrine']} · {primary['cities']} cities",
        inline=True,
    )
    if secondary:
        embed.add_field(
            name="🔄 Secondary",
            value=f"**{secondary['name']}**\n{secondary['doctrine']} · {secondary['cities']} cities",
            inline=True,
        )
    embed.add_field(name="⚔️ Military", value=military_role, inline=True)
    embed.add_field(name="🎖️ Squad",    value=squad_role,    inline=True)
    return embed


class RegistrationCardView(discord.ui.View):
    """Attached to each registration card. Persistent so Withdraw survives restarts."""

    def __init__(self, reg_id: int):
        super().__init__(timeout=None)
        self.add_item(_WithdrawButton(reg_id))


class _WithdrawButton(discord.ui.Button):
    def __init__(self, reg_id: int):
        super().__init__(
            label="Withdraw",
            style=discord.ButtonStyle.danger,
            emoji="🚪",
            custom_id=f"withdraw_reg_{reg_id}",
        )
        self._reg_id = reg_id

    async def callback(self, interaction: discord.Interaction) -> None:
        reg = await _fetch_reg(self._reg_id)
        if not reg:
            await interaction.response.send_message("Registration not found.", ephemeral=True)
            return
        if reg["user_id"] != interaction.user.id:
            await interaction.response.send_message(
                "Only the registered player can withdraw.", ephemeral=True
            )
            return
        if reg["status"] == "withdrawn":
            await interaction.response.send_message("Already withdrawn.", ephemeral=True)
            return

        match = await db.get_match_by_channel(interaction.channel_id)
        if match and match["status"] == "locked":
            await interaction.response.send_message(
                "The roster is locked — contact the Map Leader to withdraw.", ephemeral=True
            )
            return

        await db.withdraw_registration(self._reg_id)

        # Strike-through the card
        orig = interaction.message.embeds[0]
        struck = discord.Embed(
            title=f"~~{orig.title}~~  —  WITHDRAWN",
            color=discord.Color.dark_grey(),
        )
        await interaction.message.edit(embed=struck, view=None)
        await interaction.response.send_message("You've withdrawn from this match.", ephemeral=True)


async def _fetch_reg(reg_id: int) -> Optional[dict]:
    import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM registrations WHERE id = ?", (reg_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


def _squad_desc(role: str) -> str:
    return {
        "Leader":  "Match commander (1 per match)",
        "Scout":   "Second-in-command (1 per match)",
        "Spy":     "Covert ops — full map access (1 per match)",
        "Soldier": "Standard combatant (unlimited)",
    }.get(role, "")
