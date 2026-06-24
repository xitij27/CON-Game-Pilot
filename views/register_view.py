"""Registration flow: persistent Register button → role selects → country modal → card."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
import discord

import config
import database as db
from data.game_data import get_countries, get_all_countries, find_country, find_country_in_region
from views.setup_views import _TZ_OPTIONS

# Ephemeral state keyed by user_id while they fill in the country modal
_pending: dict[int, dict] = {}

DOCTRINE_COLORS = {
    "Western":  discord.Color.blue(),
    "Eastern":  discord.Color.red(),
    "European": discord.Color.gold(),
}

_DOCTRINE_EMOJI = {"Western": "🟦", "Eastern": "🟥", "European": "🟨"}


async def _update_roster_embed(match: dict, channel: discord.TextChannel) -> None:
    """Edit the pinned roster message to show only unclaimed primary countries."""
    roster_msg_id = match.get("roster_message_id")
    if not roster_msg_id:
        return
    try:
        msg = await channel.fetch_message(roster_msg_id)
    except (discord.NotFound, discord.Forbidden):
        return
    if not msg.embeds:
        return

    taken_primary = await db.get_taken_primary_countries(match["id"])
    all_countries = get_countries(match["game_type"], match["region"])
    available = [c for c in all_countries if c["name"].lower() not in taken_primary]

    if available:
        lines = [
            f"{_DOCTRINE_EMOJI.get(c['doctrine'], '⬜')} **{c['name']}**  ·  "
            f"{c['doctrine']}  ·  {c['cities']} cities"
            for c in available
        ]
        new_value = "\n".join(lines)
    else:
        new_value = "*(all countries claimed)*"

    embed = msg.embeds[0]
    for i, field in enumerate(embed.fields):
        if field.name == "Available Countries":
            embed.set_field_at(i, name="Available Countries", value=new_value, inline=False)
            break

    await msg.edit(embed=embed)


# ── Persistent channel panel (pinned roster message view, status-aware) ──────

class MatchChannelView(discord.ui.View):
    """
    Pinned message view for a match channel.
    Buttons shown depend on match status:
      open   → Register + Lock Roster + View Registrations + Cancel Match
      locked → Unlock Roster + Start Game + View Registrations + Cancel Match
      other  → no buttons
    """

    def __init__(self, channel_id: int, status: str = "open"):
        super().__init__(timeout=None)
        if status == "open":
            self.add_item(_RegisterButton(channel_id))
            self.add_item(_LockRosterButton(channel_id))
            self.add_item(_ViewRegistrationsButton(channel_id))
            self.add_item(_EditScheduleButton(channel_id))
            self.add_item(_CancelMatchButton(channel_id))
        elif status == "locked":
            self.add_item(_UnlockRosterButton(channel_id))
            self.add_item(_StartGameChannelButton(channel_id))
            self.add_item(_ViewRegistrationsButton(channel_id))
            self.add_item(_EditScheduleButton(channel_id))
            self.add_item(_CancelMatchButton(channel_id))


# Backward-compat alias used by hub_view imports
RegisterMatchView = MatchChannelView


async def update_channel_panel(
    match: dict,
    channel: discord.TextChannel,
    bot,
    status: str | None = None,
) -> None:
    """Swap the view on the pinned roster message to reflect current status."""
    roster_msg_id = match.get("roster_message_id")
    if not roster_msg_id:
        return
    try:
        msg = await channel.fetch_message(roster_msg_id)
    except (discord.NotFound, discord.Forbidden):
        return

    current_status = status or match["status"]
    if current_status in ("started", "won", "lost", "cancelled"):
        await msg.edit(view=None)
    else:
        view = MatchChannelView(match["channel_id"], current_status)
        bot.add_view(view)
        await msg.edit(view=view)


class _RegisterButton(discord.ui.Button):
    def __init__(self, channel_id: int):
        super().__init__(
            label="Register",
            style=discord.ButtonStyle.primary,
            emoji="📋",
            custom_id=f"register_match_{channel_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            match = await db.get_match_by_channel(interaction.channel_id)
            if not match:
                await interaction.response.send_message("This match no longer exists.", ephemeral=True)
                return
            if match["status"] != "open":
                if match["status"] in ("won", "lost"):
                    msg = "This game has already ended — registration is closed."
                elif match["status"] == "started":
                    msg = "This game is already in progress — registration is closed."
                else:
                    msg = "Registration is closed — the roster for this match has been locked."
                await interaction.response.send_message(msg, ephemeral=True)
                return

            existing = await db.get_registration(match["id"], interaction.user.id)
            if existing:
                await interaction.response.send_message(
                    "You're already registered. Use the **Withdraw** button on your registration card to opt out.",
                    ephemeral=True,
                )
                return

            sq_counts = await db.get_squad_role_counts(match["id"])
            taken_mil = await db.get_taken_military_roles(match["id"])
            is_leader = interaction.user.id == match["leader_id"]
            view = _RoleSelectionView(match, sq_counts, taken_mil, is_leader=is_leader)
            await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong. Please try again.", ephemeral=True
                )


# ── Lock Roster button ───────────────────────────────────────────────────────

class _LockRosterButton(discord.ui.Button):
    def __init__(self, channel_id: int):
        self._channel_id = channel_id
        super().__init__(
            label="Lock Roster",
            style=discord.ButtonStyle.danger,
            emoji="🔒",
            custom_id=f"lock_roster_ch_{channel_id}",
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
                    "Only the Match Leader or an Admin can lock the roster.", ephemeral=True
                )
                return
            if match["status"] != "open":
                await interaction.response.send_message(
                    "The roster is already locked.", ephemeral=True
                )
                return
            regs    = await db.get_registrations(match["id"])
            members = {r["user_id"]: interaction.guild.get_member(r["user_id"]) for r in regs}
            from views.roster_view import RosterPanel  # lazy — avoids circular import
            panel = RosterPanel(match, regs, members)
            await interaction.response.send_message(embed=panel.build_embed(), view=panel, ephemeral=True)
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong. Please try again.", ephemeral=True
                )


# ── Unlock Roster button ──────────────────────────────────────────────────────

class _UnlockRosterButton(discord.ui.Button):
    def __init__(self, channel_id: int):
        self._channel_id = channel_id
        super().__init__(
            label="Unlock Roster",
            style=discord.ButtonStyle.secondary,
            emoji="🔓",
            custom_id=f"unlock_roster_ch_{channel_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            match = await db.get_match_by_channel(self._channel_id)
            if not match:
                await interaction.response.send_message("Match not found.", ephemeral=True)
                return
            if interaction.user.id != match["leader_id"]:
                await interaction.response.send_message(
                    "Only the Match Leader can unlock the roster.", ephemeral=True
                )
                return
            if match["status"] != "locked":
                await interaction.response.send_message(
                    "The roster isn't locked.", ephemeral=True
                )
                return
            cog = interaction.client.cogs.get("MatchCog")
            if cog:
                await cog.do_unlock_roster(interaction, match)
            else:
                await interaction.response.send_message("Bot error — try again.", ephemeral=True)
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong. Please try again.", ephemeral=True
                )


# ── Start Game button + modal ─────────────────────────────────────────────────

class _StartGameChannelButton(discord.ui.Button):
    def __init__(self, channel_id: int):
        self._channel_id = channel_id
        super().__init__(
            label="Start Game",
            style=discord.ButtonStyle.success,
            emoji="🎮",
            custom_id=f"startgame_ch_{channel_id}",
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
                    "Only the Match Leader or an Admin can start the game.", ephemeral=True
                )
                return
            if match["status"] != "locked":
                await interaction.response.send_message(
                    "The roster must be locked before starting the game.", ephemeral=True
                )
                return
            await interaction.response.send_modal(_StartGameChannelModal(match))
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong. Please try again.", ephemeral=True
                )


class _StartGameChannelModal(discord.ui.Modal):
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
            await interaction.response.send_message("Bot error — try again.", ephemeral=True)


# ── Cancel Match button (leader only) ────────────────────────────────────────

class _CancelMatchButton(discord.ui.Button):
    def __init__(self, channel_id: int):
        self._channel_id = channel_id
        super().__init__(
            label="Cancel Match",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"cancel_match_ch_{channel_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            match = await db.get_match_by_channel(self._channel_id)
            if not match:
                await interaction.response.send_message("Match not found.", ephemeral=True)
                return
            if interaction.user.id != match["leader_id"]:
                await interaction.response.send_message(
                    "Only the Match Leader can cancel this match.", ephemeral=True
                )
                return
            if match["status"] in ("started", "won", "lost"):
                await interaction.response.send_message(
                    "The game is already in progress — use `/endgame` to declare the outcome.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="⚠️  Cancel this match?",
                    description="The channel and scheduled event will be **permanently deleted**.",
                    color=discord.Color.red(),
                ),
                view=_CancelMatchConfirmView(match),
                ephemeral=True,
            )
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong. Please try again.", ephemeral=True
                )


class _CancelMatchConfirmView(discord.ui.View):
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

    @discord.ui.button(label="Keep Match", style=discord.ButtonStyle.secondary)
    async def keep(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="Cancellation aborted.", view=None)
        self.stop()


# ── Edit Schedule button + view ───────────────────────────────────────────────

class _EditScheduleButton(discord.ui.Button):
    def __init__(self, channel_id: int):
        self._channel_id = channel_id
        super().__init__(
            label="Edit Schedule",
            style=discord.ButtonStyle.secondary,
            emoji="📅",
            custom_id=f"edit_schedule_ch_{channel_id}",
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
                    "Only the Match Leader or an Admin can edit the schedule.", ephemeral=True
                )
                return
            if match["status"] in ("started", "won", "lost", "cancelled"):
                await interaction.response.send_message(
                    "The schedule can only be edited before the game starts.", ephemeral=True
                )
                return
            view = _EditScheduleView(match)
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="📅  Edit Match Schedule",
                    description="Select your timezone to set a new start time.",
                    color=discord.Color.blue(),
                ),
                view=view,
                ephemeral=True,
            )
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong. Please try again.", ephemeral=True
                )


class _EditScheduleView(discord.ui.View):
    """Ephemeral multi-step view: timezone → date/time → confirm → edit Discord event."""

    def __init__(self, match: dict):
        super().__init__(timeout=300)
        self.match        = match
        self._tz_offset: Optional[int] = None
        self._date: Optional[str]      = None
        self._hour: Optional[int]      = None
        self._minute: Optional[int]    = None
        self._duration_minutes: int    = 60
        self._add_timezone_select()

    # ── step builders ─────────────────────────────────────────────────────────

    def _add_timezone_select(self) -> None:
        self.clear_items()
        options = [
            discord.SelectOption(label=label, value=str(offset), default=(self._tz_offset == offset))
            for offset, label in _TZ_OPTIONS
        ]
        sel = discord.ui.Select(placeholder="🌍  Your timezone...", options=options)
        sel.callback = self._on_tz
        self.add_item(sel)

    def _add_time_selects(self) -> None:
        self.clear_items()
        now = datetime.now(timezone.utc)

        date_options = []
        for i in range(14):
            d = now.date() + timedelta(days=i)
            val = d.isoformat()
            day_str = d.strftime("%a, %b %-d")
            if i == 0:
                label = f"Today — {day_str}"
            elif i == 1:
                label = f"Tomorrow — {day_str}"
            else:
                label = day_str
            date_options.append(discord.SelectOption(label=label, value=val, default=(self._date == val)))
        date_sel = discord.ui.Select(placeholder="📅  New start date...", options=date_options, row=0)
        date_sel.callback = self._on_date
        self.add_item(date_sel)

        hour_options = [
            discord.SelectOption(label=f"{h:02d}:__", value=str(h), default=(self._hour == h))
            for h in range(24)
        ]
        hour_sel = discord.ui.Select(placeholder="🕐  Start hour...", options=hour_options, row=1)
        hour_sel.callback = self._on_hour
        self.add_item(hour_sel)

        minute_options = [
            discord.SelectOption(label=f"__{m:02d}", value=str(m), default=(self._minute == m))
            for m in (0, 15, 30, 45)
        ]
        minute_sel = discord.ui.Select(placeholder="⏱  Start minute...", options=minute_options, row=2)
        minute_sel.callback = self._on_minute
        self.add_item(minute_sel)

        all_set = all(x is not None for x in (self._date, self._hour, self._minute))
        confirm_btn = discord.ui.Button(
            label="Confirm Time →",
            style=discord.ButtonStyle.primary,
            disabled=not all_set,
            row=3,
        )
        confirm_btn.callback = self._on_confirm
        self.add_item(confirm_btn)

        back_btn = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary, emoji="◀️", row=3)
        back_btn.callback = self._on_back
        self.add_item(back_btn)

    def _tz_embed(self) -> discord.Embed:
        sign = "+" if (self._tz_offset or 0) >= 0 else ""
        tz_str = f"UTC{sign}{self._tz_offset}" if self._tz_offset is not None else ""
        desc = f"Times are in **{tz_str}** (your local time)." if tz_str else "Select your timezone."
        return discord.Embed(title="📅  Edit Match Schedule", description=desc, color=discord.Color.blue())

    # ── callbacks ─────────────────────────────────────────────────────────────

    async def _on_tz(self, interaction: discord.Interaction) -> None:
        self._tz_offset = int(interaction.data["values"][0])
        self._date = self._hour = self._minute = None
        self._add_time_selects()
        await interaction.response.edit_message(embed=self._tz_embed(), view=self)

    async def _on_date(self, interaction: discord.Interaction) -> None:
        self._date = interaction.data["values"][0]
        self._add_time_selects()
        await interaction.response.edit_message(embed=self._tz_embed(), view=self)

    async def _on_hour(self, interaction: discord.Interaction) -> None:
        self._hour = int(interaction.data["values"][0])
        self._add_time_selects()
        await interaction.response.edit_message(embed=self._tz_embed(), view=self)

    async def _on_minute(self, interaction: discord.Interaction) -> None:
        self._minute = int(interaction.data["values"][0])
        self._add_time_selects()
        await interaction.response.edit_message(embed=self._tz_embed(), view=self)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        self._date = self._hour = self._minute = None
        self._add_timezone_select()
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="📅  Edit Match Schedule",
                description="Select your timezone.",
                color=discord.Color.blue(),
            ),
            view=self,
        )

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        # All validation is synchronous — do it before any response call
        local_start = datetime.fromisoformat(self._date).replace(
            hour=self._hour, minute=self._minute, second=0, microsecond=0
        )
        start_utc = (local_start - timedelta(hours=self._tz_offset)).replace(tzinfo=timezone.utc)

        if start_utc <= datetime.now(timezone.utc):
            await interaction.response.send_message(
                "Start time must be in the future.", ephemeral=True
            )
            return

        end_utc = start_utc + timedelta(minutes=self._duration_minutes)

        event_id = self.match.get("event_id")
        if not event_id:
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="⚠️  No Event Found",
                    description="This match has no associated Discord event — it may have been created before events were supported.",
                    color=discord.Color.orange(),
                ),
                view=None,
            )
            return

        # Defer BEFORE the slow Discord API calls (fetch + edit can exceed 3 s)
        await interaction.response.defer(ephemeral=True)

        try:
            event = await interaction.guild.fetch_scheduled_event(event_id)
            await event.edit(start_time=start_utc, end_time=end_utc)
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="✅  Schedule Updated",
                    description=(
                        f"**New start:** <t:{int(start_utc.timestamp())}:F>\n"
                        f"**New end:** <t:{int(end_utc.timestamp())}:F>"
                    ),
                    color=discord.Color.green(),
                ),
                view=None,
            )
        except discord.NotFound:
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="⚠️  Event Not Found",
                    description="The Discord event appears to have been deleted.",
                    color=discord.Color.orange(),
                ),
                view=None,
            )
        except discord.HTTPException:
            await interaction.followup.send(
                "Failed to update the Discord event. Please try again.", ephemeral=True
            )


# ── View Registrations button ─────────────────────────────────────────────────

class _ViewRegistrationsButton(discord.ui.Button):
    def __init__(self, channel_id: int):
        self._channel_id = channel_id
        super().__init__(
            label="Registrations",
            style=discord.ButtonStyle.secondary,
            emoji="👥",
            custom_id=f"view_regs_{channel_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            match = await db.get_match_by_channel(self._channel_id)
            if not match:
                await interaction.response.send_message("Match not found.", ephemeral=True)
                return

            regs = await db.get_registrations(match["id"])
            if not regs:
                await interaction.response.send_message(
                    "No one has registered yet.", ephemeral=True
                )
                return

            lines = []
            for reg in regs:
                member = interaction.guild.get_member(reg["user_id"])
                name = member.display_name if member else f"<@{reg['user_id']}>"
                sec = f" + **{reg['secondary_country']}**" if reg["secondary_country"] else ""
                lines.append(
                    f"**{name}** — {reg['military_role'] or '—'} · {reg['squad_role']}\n"
                    f"　🎯 **{reg['primary_country']}**{sec}"
                )

            embed = discord.Embed(
                title=f"👥  Registrations  ·  {match['game_type']} / {match['region']}",
                description="\n\n".join(lines),
                color=discord.Color.blurple(),
            )
            embed.set_footer(text=f"{len(regs)} player(s) registered")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong. Please try again.", ephemeral=True
                )


# ── Step 1: role selects ──────────────────────────────────────────────────────

class _RoleSelectionView(discord.ui.View):
    def __init__(self, match: dict, sq_counts: dict, taken_mil: list, is_leader: bool = False):
        super().__init__(timeout=120)
        self.match = match
        self.is_leader = is_leader
        self.squad_role: Optional[str] = "Leader" if is_leader else None
        self.military_role: Optional[str] = None
        self._available_squad = [
            r for r in config.SQUAD_ROLES
            if r != "Leader"
            and (
                config.SQUAD_ROLE_LIMITS.get(r) is None
                or sq_counts.get(r, 0) < config.SQUAD_ROLE_LIMITS[r]
            )
        ]
        # Leaders are never blocked by taken military roles — they always see
        # all roles and bypass the slot check (their slot is always reserved).
        if is_leader:
            self._available_mil = list(config.MILITARY_ROLES)
            self._taken_mil = set(taken_mil)
        else:
            self._available_mil = [r for r in config.MILITARY_ROLES if r not in taken_mil]
            self._taken_mil = set()
        self._rebuild()

    def build_embed(self) -> discord.Embed:
        if self.is_leader:
            desc = (
                f"**{self.match['game_type']} · {self.match['region']}**\n\n"
                "You are the **Match Leader** — Squad Role is set to **Leader** automatically.\n"
                "Pick your **Military Role**, then click **Continue**."
            )
        elif self.squad_role == "Spy":
            desc = (
                f"**{self.match['game_type']} · {self.match['region']}**\n\n"
                "**Spy** selected — no military role required.\n"
                "Click **Continue** to pick your countries."
            )
        else:
            desc = (
                f"**{self.match['game_type']} · {self.match['region']}**\n\n"
                "Pick your **Squad Role** and **Military Role**, then click **Continue**.\n"
                "-# Spy role requires no military role and unlocks all countries across the full map."
            )
        return discord.Embed(title="Registration — Step 1 of 2", description=desc, color=discord.Color.blue())

    def _rebuild(self) -> None:
        self.clear_items()

        if not self.is_leader:
            squad_select = discord.ui.Select(
                placeholder="Squad Role...",
                options=[
                    discord.SelectOption(
                        label=r, value=r, description=_squad_desc(r),
                        default=(r == self.squad_role),
                    )
                    for r in self._available_squad
                ],
                custom_id="squad_role_select",
            )
            squad_select.callback = self._on_squad_role
            self.add_item(squad_select)

        if self.squad_role != "Spy":
            if self._available_mil:
                mil_select = discord.ui.Select(
                    placeholder="Military Role...",
                    options=[
                        discord.SelectOption(
                            label=r, value=r, default=(r == self.military_role),
                            description="(taken by another player)" if r in self._taken_mil else "",
                        )
                        for r in self._available_mil
                    ],
                    custom_id="military_role_select",
                )
                mil_select.callback = self._on_military_role
                self.add_item(mil_select)
            else:
                mil_select = discord.ui.Select(
                    placeholder="No military roles available",
                    options=[discord.SelectOption(label="—", value="none")],
                    disabled=True,
                    custom_id="military_role_select",
                )
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
        if self.squad_role == "Spy":
            self.military_role = None
        self._rebuild()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _on_military_role(self, interaction: discord.Interaction) -> None:
        self.military_role = interaction.data["values"][0]
        await interaction.response.defer()

    async def _on_continue(self, interaction: discord.Interaction) -> None:
        is_spy = self.squad_role == "Spy"
        if not self.squad_role:
            await interaction.response.send_message(
                "Please select a Squad Role first.", ephemeral=True
            )
            return
        if not is_spy and not self.military_role:
            if not self._available_mil:
                await interaction.response.send_message(
                    "All military roles are filled. Only **Spy** can still register.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "Please select a Military Role first.", ephemeral=True
                )
            return

        match_id = self.match["id"]

        # Non-leaders may never claim the Leader squad role
        if self.squad_role == "Leader" and interaction.user.id != self.match["leader_id"]:
            await interaction.response.send_message(
                "Only the Match Leader can take the **Leader** squad role.", ephemeral=True
            )
            return

        # Validate squad role slot (Leader's own slot is always reserved for them)
        if self.squad_role != "Leader":
            sq_counts = await db.get_squad_role_counts(match_id)
            limit = config.SQUAD_ROLE_LIMITS.get(self.squad_role)
            if limit is not None and sq_counts.get(self.squad_role, 0) >= limit:
                await interaction.response.send_message(
                    f"The **{self.squad_role}** slot is already filled. Choose a different Squad Role.",
                    ephemeral=True,
                )
                return

        # Validate military role slot (Spy has no military role; leader bypasses taken check)
        if not is_spy and not self.is_leader:
            taken_mil = await db.get_taken_military_roles(match_id)
            if self.military_role in taken_mil:
                free = ", ".join(r for r in config.MILITARY_ROLES if r not in taken_mil)
                await interaction.response.send_message(
                    f"**{self.military_role}** is taken. Available: {free or 'none'}",
                    ephemeral=True,
                )
                return

        if self.squad_role == "Spy":
            # Spy searches across all regions — too many options for a dropdown
            _pending[interaction.user.id] = {
                "match_id":      match_id,
                "game_type":     self.match["game_type"],
                "region":        self.match["region"],
                "squad_role":    self.squad_role,
                "military_role": self.military_role,
            }
            await interaction.response.send_modal(_CountryModal(self.match))
        else:
            taken_primary = await db.get_taken_primary_countries(match_id)
            all_countries = get_countries(self.match["game_type"], self.match["region"])
            primary_available = [c for c in all_countries if c["name"].lower() not in taken_primary]
            if not primary_available:
                await interaction.response.send_message(
                    "No countries are available in this region right now.", ephemeral=True
                )
                return
            view = _CountrySelectView(self.match, primary_available, self.squad_role, self.military_role, is_leader=self.is_leader)
            embed = discord.Embed(
                title="Registration — Step 2 of 2",
                description=(
                    f"**{self.match['game_type']} · {self.match['region']}**\n\n"
                    "Choose your **Primary Country** and optionally a **Secondary Country**,\n"
                    "then click **Register**."
                ),
                color=discord.Color.blue(),
            )
            await interaction.response.edit_message(embed=embed, view=view)


# ── Step 2a: country dropdowns (non-Spy) ─────────────────────────────────────

class _CountrySelectView(discord.ui.View):
    def __init__(self, match: dict, primary_available: list[dict], squad_role: str, military_role: str, is_leader: bool = False):
        super().__init__(timeout=120)
        self.match = match
        self.squad_role = squad_role
        self.military_role = military_role
        self.is_leader = is_leader
        self.primary_country: Optional[str] = None
        self.secondary_country: Optional[str] = None

        primary_options = [
            discord.SelectOption(
                label=c["name"],
                value=c["name"],
                description=f"{c['doctrine']} · {c['cities']} cities",
            )
            for c in primary_available[:25]
        ]
        secondary_options = [
            discord.SelectOption(
                label=c["name"],
                value=c["name"],
                description=f"{c['doctrine']} · {c['cities']} cities",
            )
            for c in primary_available[:24]
        ]

        primary_sel = discord.ui.Select(
            placeholder="Primary Country...",
            options=primary_options,
            custom_id="primary_country_select",
        )
        primary_sel.callback = self._on_primary
        self.add_item(primary_sel)

        # Secondary: None option + available countries (Discord limit is 25 total)
        secondary_sel = discord.ui.Select(
            placeholder="Secondary Country (optional)...",
            options=[discord.SelectOption(label="— None —", value="__none__")] + secondary_options,
            custom_id="secondary_country_select",
        )
        secondary_sel.callback = self._on_secondary
        self.add_item(secondary_sel)

        submit = discord.ui.Button(
            label="Register",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id="country_submit",
        )
        submit.callback = self._on_submit
        self.add_item(submit)

    async def _on_primary(self, interaction: discord.Interaction) -> None:
        self.primary_country = interaction.data["values"][0]
        await interaction.response.defer()

    async def _on_secondary(self, interaction: discord.Interaction) -> None:
        val = interaction.data["values"][0]
        self.secondary_country = None if val == "__none__" else val
        await interaction.response.defer()

    async def _on_submit(self, interaction: discord.Interaction) -> None:
        try:
            if not self.primary_country:
                await interaction.response.send_message(
                    "Please select a Primary Country first.", ephemeral=True
                )
                return

            game_type = self.match["game_type"]
            region    = self.match["region"]
            match_id  = self.match["id"]

            primary_c   = find_country_in_region(game_type, region, self.primary_country)
            secondary_c = find_country_in_region(game_type, region, self.secondary_country) if self.secondary_country else None

            if secondary_c and secondary_c["name"].lower() == primary_c["name"].lower():
                await interaction.response.send_message(
                    "Primary and Secondary country can't be the same.", ephemeral=True
                )
                return

            # Race-condition re-check: primary countries must be unique; secondary may not reuse one either
            taken_primary = await db.get_taken_primary_countries(match_id)
            if primary_c["name"].lower() in taken_primary:
                await interaction.response.send_message(
                    f"**{primary_c['name']}** was just claimed — please restart registration.", ephemeral=True
                )
                return
            if secondary_c and secondary_c["name"].lower() in taken_primary:
                await interaction.response.send_message(
                    f"**{secondary_c['name']}** was just claimed as a primary country — please restart registration.", ephemeral=True
                )
                return

            if self.squad_role != "Leader":
                sq_counts = await db.get_squad_role_counts(match_id)
                limit = config.SQUAD_ROLE_LIMITS.get(self.squad_role)
                if limit is not None and sq_counts.get(self.squad_role, 0) >= limit:
                    await interaction.response.send_message(
                        f"**{self.squad_role}** was just taken — please restart registration.", ephemeral=True
                    )
                    return

            if not self.is_leader:
                taken_mil = await db.get_taken_military_roles(match_id)
                if self.military_role in taken_mil:
                    await interaction.response.send_message(
                        f"**{self.military_role}** was just taken — please restart registration.", ephemeral=True
                    )
                    return

            reg_id = await db.create_registration(
                match_id, interaction.user.id,
                primary_c["name"],
                secondary_c["name"] if secondary_c else None,
                self.military_role, self.squad_role,
            )
            if reg_id is None:
                await interaction.response.send_message("You're already registered.", ephemeral=True)
                return

            card_embed = _build_card(interaction.user, primary_c, secondary_c, self.military_role, self.squad_role)
            card_view  = RegistrationCardView(reg_id)

            await interaction.response.edit_message(
                embed=discord.Embed(title="✅  Registered!", color=discord.Color.green()),
                view=None,
            )
            msg = await interaction.followup.send(embed=card_embed, view=card_view)
            await db.update_registration_message(reg_id, msg.id)
            await _update_roster_embed(self.match, interaction.channel)
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong. Please try again.", ephemeral=True
                )


# ── Step 2b: country name modal (Spy only) ────────────────────────────────────

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

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
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

            # Primary countries must be unique; secondary may not reuse a taken primary either
            taken_primary = await db.get_taken_primary_countries(match_id)
            if primary_c["name"].lower() in taken_primary:
                await interaction.response.send_message(
                    f"**{primary_c['name']}** is already claimed as a primary country.", ephemeral=True
                )
                return
            if secondary_c and secondary_c["name"].lower() in taken_primary:
                await interaction.response.send_message(
                    f"**{secondary_c['name']}** is already claimed as a primary country — choose a different secondary.", ephemeral=True
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

            if military_role:
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

            match = await db.get_match_by_channel(interaction.channel_id)
            if match and interaction.channel:
                await _update_roster_embed(match, interaction.channel)
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong. Please try again.", ephemeral=True
                )


# ── Registration card + withdraw button ───────────────────────────────────────

def _build_card(
    user: discord.Member,
    primary: dict,
    secondary: Optional[dict],
    military_role: Optional[str],
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
    if military_role:
        embed.add_field(name="⚔️ Military", value=military_role, inline=True)
    embed.add_field(name="🎖️ Squad", value=squad_role, inline=True)
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
        try:
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
            if match and match["leader_id"] == interaction.user.id:
                await interaction.response.send_message(
                    "As the Match Leader you cannot withdraw — use the **Cancel Match** button to cancel the match.",
                    ephemeral=True,
                )
                return
            if match and match["status"] != "open":
                if match["status"] in ("won", "lost"):
                    msg = "This game has already ended — withdrawal is not possible."
                elif match["status"] == "started":
                    msg = "This game is already in progress — withdrawal is not possible."
                else:
                    msg = "The roster is locked — withdrawal is not possible."
                await interaction.response.send_message(msg, ephemeral=True)
                return

            await db.withdraw_registration(self._reg_id)

            orig = interaction.message.embeds[0]
            struck = discord.Embed(
                title=f"~~{orig.title}~~  —  WITHDRAWN",
                color=discord.Color.dark_grey(),
            )
            await interaction.message.edit(embed=struck, view=None)
            await interaction.response.send_message("You've withdrawn from this match.", ephemeral=True)

            if match:
                await _update_roster_embed(match, interaction.channel)
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong. Please try again.", ephemeral=True
                )


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
