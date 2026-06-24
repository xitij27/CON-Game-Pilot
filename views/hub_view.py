"""Hub channel views: control panel, per-match cards, and match management panel."""
from __future__ import annotations

import asyncio
import discord

import config
import database as db
from hub_utils import refresh_hub_card
from views.register_view import _RegisterButton
from views.roster_view import RosterPanel


# ── Top-level control panel (one persistent message in match-hub) ─────────────

class MatchHubControlView(discord.ui.View):
    """Persistent control bar pinned in match-hub. Survives restarts."""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(_CreateMatchButton())


class _CreateMatchButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Create Match",
            style=discord.ButtonStyle.success,
            emoji="🗺️",
            custom_id="hub_create_match",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        if not any(r.name in config.ALLOWED_RANKS for r in member.roles):
            await interaction.response.send_message(
                f"You need at least **{config.ALLOWED_RANKS[0]}** rank to create a match.",
                ephemeral=True,
            )
            return

        cog = interaction.client.cogs.get("MatchCog")
        if not cog:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a moment.", ephemeral=True
            )
            return

        await cog.creategame_from_interaction(interaction)


# ── Per-match card view (informational only — no buttons) ────────────────────

class MatchCardView(discord.ui.View):
    """Read-only card in match-hub. Buttons live on the pinned channel message."""

    def __init__(self, channel_id: int):
        super().__init__(timeout=None)


class _ManageMatchButton(discord.ui.Button):
    def __init__(self, channel_id: int):
        self._channel_id = channel_id
        super().__init__(
            label="Manage",
            style=discord.ButtonStyle.secondary,
            emoji="⚙️",
            custom_id=f"hub_manage_{channel_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            match = await db.get_match_by_channel(self._channel_id)
            if not match:
                await interaction.response.send_message("Match not found.", ephemeral=True)
                return

            is_leader = interaction.user.id == match["leader_id"]
            is_admin  = any(r.name in config.ADMIN_ROLES for r in interaction.user.roles)

            if not is_leader and not is_admin:
                await interaction.response.send_message(
                    "Only the Match Leader or an Admin can manage this match.", ephemeral=True
                )
                return

            status = match["status"]
            if status in ("won", "lost", "cancelled"):
                await interaction.response.send_message(
                    "This match has already ended.", ephemeral=True
                )
                return

            regs    = await db.get_registrations(match["id"])
            members = {r["user_id"]: interaction.guild.get_member(r["user_id"]) for r in regs}

            panel = _MatchManagePanel(match, regs, members, is_leader, is_admin)
            await interaction.response.send_message(
                embed=panel.build_embed(), view=panel, ephemeral=True
            )
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong. Please try again.", ephemeral=True
                )


# ── Ephemeral match management panel ─────────────────────────────────────────

class _MatchManagePanel(discord.ui.View):
    """Ephemeral panel showing actions available to the leader/admin for a match."""

    def __init__(
        self,
        match: dict,
        regs: list[dict],
        members: dict[int, discord.Member],
        is_leader: bool,
        is_admin: bool,
    ):
        super().__init__(timeout=120)
        self.match     = match
        self.regs      = regs
        self.members   = members
        self.is_leader = is_leader
        self.is_admin  = is_admin
        self._build_buttons()

    def _build_buttons(self) -> None:
        self.clear_items()
        status = self.match["status"]

        if status == "open":
            roster_btn = discord.ui.Button(
                label="Open Roster Panel",
                style=discord.ButtonStyle.primary,
                emoji="📋",
            )
            roster_btn.callback = self._on_roster
            self.add_item(roster_btn)

            cancel_btn = discord.ui.Button(
                label="Cancel Match",
                style=discord.ButtonStyle.danger,
                emoji="❌",
            )
            cancel_btn.callback = self._on_cancel
            self.add_item(cancel_btn)

        elif status == "locked":
            if self.is_leader:
                unlock_btn = discord.ui.Button(
                    label="Unlock Roster",
                    style=discord.ButtonStyle.secondary,
                    emoji="🔓",
                )
                unlock_btn.callback = self._on_unlock
                self.add_item(unlock_btn)

            start_btn = discord.ui.Button(
                label="Enter Game Code",
                style=discord.ButtonStyle.success,
                emoji="🎮",
            )
            start_btn.callback = self._on_start
            self.add_item(start_btn)

            cancel_btn = discord.ui.Button(
                label="Cancel Match",
                style=discord.ButtonStyle.danger,
                emoji="❌",
            )
            cancel_btn.callback = self._on_cancel
            self.add_item(cancel_btn)

        elif status == "started":
            won_btn = discord.ui.Button(
                label="Won",
                style=discord.ButtonStyle.success,
                emoji="🏆",
            )
            won_btn.callback = self._on_won
            self.add_item(won_btn)

            lost_btn = discord.ui.Button(
                label="Lost",
                style=discord.ButtonStyle.danger,
                emoji="💀",
            )
            lost_btn.callback = self._on_lost
            self.add_item(lost_btn)

    def build_embed(self) -> discord.Embed:
        status = self.match["status"]
        action_hint = {
            "open":    "Open the roster panel to select players and lock the roster, or cancel the match.",
            "locked":  "Enter the game lobby code to start, unlock the roster to reopen registration, or cancel.",
            "started": "Declare the game outcome once it's finished.",
        }.get(status, "")
        embed = discord.Embed(
            title=f"⚙️  Manage  ·  {self.match['game_type']}  /  {self.match['region']}",
            description=action_hint,
            color=discord.Color.orange(),
        )
        embed.add_field(name="Status", value=status.title(), inline=True)
        embed.add_field(name="Players", value=str(len(self.regs)), inline=True)
        return embed

    # ── action callbacks ──────────────────────────────────────────────────────

    async def _on_roster(self, interaction: discord.Interaction) -> None:
        panel = RosterPanel(self.match, self.regs, self.members)
        await interaction.response.edit_message(embed=panel.build_embed(), view=panel)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        confirm = _CancelConfirmView(self.match)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="⚠️  Cancel this match?",
                description="The match channel will be **deleted** in 5 seconds.",
                color=discord.Color.red(),
            ),
            view=confirm,
        )

    async def _on_unlock(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.cogs.get("MatchCog")
        if cog:
            await cog.do_unlock_roster(interaction, self.match)
        else:
            await interaction.response.send_message("Bot error — try /unlockroster.", ephemeral=True)

    async def _on_start(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(_StartGameModal(self.match))

    async def _on_won(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.cogs.get("MatchCog")
        if cog:
            await cog.do_end_game(interaction, self.match, "Won")
        else:
            await interaction.response.send_message("Bot error — try /endgame.", ephemeral=True)

    async def _on_lost(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.cogs.get("MatchCog")
        if cog:
            await cog.do_end_game(interaction, self.match, "Lost")
        else:
            await interaction.response.send_message("Bot error — try /endgame.", ephemeral=True)


# ── Start game modal (8-digit lobby code) ─────────────────────────────────────

class _StartGameModal(discord.ui.Modal):
    def __init__(self, match: dict):
        super().__init__(title="Enter Game Lobby Code")
        self.match = match
        self.code_input = discord.ui.InputText(
            label="Game Code (8 digits)",
            placeholder="12345678",
            min_length=8,
            max_length=8,
            style=discord.InputTextStyle.short,
        )
        self.add_item(self.code_input)

    async def callback(self, interaction: discord.Interaction) -> None:
        code = self.code_input.value.strip()
        if not (code.isdigit() and len(code) == 8):
            await interaction.response.send_message(
                "Game code must be exactly **8 digits**.", ephemeral=True
            )
            return

        cog = interaction.client.cogs.get("MatchCog")
        if cog:
            await cog.do_start_game(interaction, self.match, code)
        else:
            await interaction.response.send_message("Bot error — try /startgame.", ephemeral=True)


# ── Cancel confirm ────────────────────────────────────────────────────────────

class _CancelConfirmView(discord.ui.View):
    def __init__(self, match: dict):
        super().__init__(timeout=60)
        self.match = match

    @discord.ui.button(label="Yes, Cancel Match", style=discord.ButtonStyle.danger)
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Match cancelled.",
                description="The channel will be deleted in 5 seconds.",
                color=discord.Color.dark_grey(),
            ),
            view=None,
        )
        self.stop()
        cog = interaction.client.cogs.get("MatchCog")
        if cog:
            await cog.do_cancel_match(self.match, interaction.guild)
        else:
            await db.update_match_status(self.match["id"], "cancelled")
            channel = interaction.guild.get_channel(self.match["channel_id"])
            await asyncio.sleep(5)
            if channel:
                await channel.delete(reason="Match cancelled via hub panel")

    @discord.ui.button(label="Keep Match", style=discord.ButtonStyle.secondary)
    async def keep(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="Cancellation aborted.", view=None)
        self.stop()
