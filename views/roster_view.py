"""Roster management panel — leader-only ephemeral view."""
from __future__ import annotations

import discord

import config
import database as db


class RosterPanel(discord.ui.View):
    def __init__(self, match: dict, registrations: list[dict], members: dict[int, discord.Member]):
        super().__init__(timeout=300)
        self.match = match
        self.registrations = registrations
        self.members = members
        # All registrations selected by default
        self.selected: set[int] = {r["id"] for r in registrations}

        # One toggle button per player (rows 0-3, max 20 buttons before row 4)
        for i, reg in enumerate(registrations[:20]):
            self.add_item(_PlayerToggle(reg, members.get(reg["user_id"]), i))

        lock_btn = discord.ui.Button(
            label="🔒  Lock Roster",
            style=discord.ButtonStyle.danger,
            row=4,
            custom_id="lock_roster_btn",
        )
        lock_btn.callback = self._on_lock
        self.add_item(lock_btn)

        close_btn = discord.ui.Button(
            label="Close",
            style=discord.ButtonStyle.secondary,
            row=4,
            custom_id="close_panel_btn",
        )
        close_btn.callback = self._on_close
        self.add_item(close_btn)

    # ── embed ─────────────────────────────────────────────────────────────────

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"🗺️  Roster Panel  ·  {self.match['game_type']}  /  {self.match['region']}",
            color=discord.Color.orange(),
        )
        lines = []
        for reg in self.registrations:
            member = self.members.get(reg["user_id"])
            name = member.display_name if member else f"<@{reg['user_id']}>"
            tick = "✅" if reg["id"] in self.selected else "❌"
            sec  = f" + {reg['secondary_country']}" if reg["secondary_country"] else ""
            lines.append(
                f"{tick} **{name}** — {reg['primary_country']}{sec} | {reg['military_role']} | {reg['squad_role']}"
            )
        embed.description = "\n".join(lines) if lines else "*No registrations yet.*"
        embed.set_footer(text=f"Selected: {len(self.selected)} / {len(self.registrations)}  ·  Toggle players, then Lock Roster.")
        return embed

    # ── callbacks ─────────────────────────────────────────────────────────────

    async def _on_lock(self, interaction: discord.Interaction) -> None:
        if not self.selected:
            await interaction.response.send_message("Select at least one player before locking.", ephemeral=True)
            return
        confirm_view = _LockConfirmView(self)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="⚠️  Confirm Roster Lock",
                description=(
                    f"**{len(self.selected)} player(s)** will be kept.\n"
                    "Everyone else loses channel access.\n\n**This cannot be undone.**"
                ),
                color=discord.Color.red(),
            ),
            view=confirm_view,
        )

    async def _on_close(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(title="Panel closed.", color=discord.Color.greyple()),
            view=None,
        )


class _PlayerToggle(discord.ui.Button):
    def __init__(self, reg: dict, member: discord.Member | None, idx: int):
        label = (member.display_name if member else f"User {reg['user_id']}")[:20]
        super().__init__(
            label=label,
            style=discord.ButtonStyle.success,
            row=idx // 5,
            custom_id=f"ptoggle_{reg['id']}",
        )
        self._reg_id = reg["id"]

    async def callback(self, interaction: discord.Interaction) -> None:
        panel: RosterPanel = self.view
        if self._reg_id in panel.selected:
            panel.selected.discard(self._reg_id)
            self.style = discord.ButtonStyle.danger
        else:
            panel.selected.add(self._reg_id)
            self.style = discord.ButtonStyle.success
        await interaction.response.edit_message(embed=panel.build_embed(), view=panel)


class _LockConfirmView(discord.ui.View):
    def __init__(self, panel: RosterPanel):
        super().__init__(timeout=60)
        self.panel = panel

        yes = discord.ui.Button(label="Yes — Lock It", style=discord.ButtonStyle.danger)
        yes.callback = self._confirm
        no = discord.ui.Button(label="Go Back", style=discord.ButtonStyle.secondary)
        no.callback = self._cancel
        self.add_item(yes)
        self.add_item(no)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        panel = self.panel
        guild = interaction.guild
        channel = interaction.channel

        await interaction.response.defer(ephemeral=True)

        await db.update_match_status(panel.match["id"], "locked")
        for reg in panel.registrations:
            status = "selected" if reg["id"] in panel.selected else "rejected"
            await db.update_registration_status(reg["id"], status)

        # Build permission overwrites
        overwrites: dict = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True),
        }
        for role in guild.roles:
            if role.name in config.ADMIN_ROLES:
                overwrites[role] = discord.PermissionOverwrite(
                    read_messages=True, send_messages=True, use_application_commands=True
                )

        leader = guild.get_member(panel.match["leader_id"])
        if leader:
            # Leader needs use_application_commands to run /startgame after lock
            overwrites[leader] = discord.PermissionOverwrite(
                read_messages=True, send_messages=True, use_application_commands=True
            )

        selected_members: list[discord.Member] = []
        for reg in panel.registrations:
            if reg["id"] in panel.selected:
                m = guild.get_member(reg["user_id"])
                if m:
                    overwrites[m] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
                    selected_members.append(m)

        # Remove the Register button BEFORE changing permissions. If we edit
        # permissions first, rejected players lose channel access while the button
        # is still visible — Discord then rejects their click before the bot can
        # respond with a proper "roster is locked" message, causing "interaction failed".
        roster_msg_id = panel.match.get("roster_message_id")
        if roster_msg_id:
            try:
                roster_msg = await channel.fetch_message(roster_msg_id)
                await roster_msg.edit(view=None)
            except (discord.NotFound, discord.Forbidden):
                pass

        mentions = " ".join(m.mention for m in selected_members)
        await channel.send(
            embed=discord.Embed(
                title="🔒  Roster Locked",
                description=f"The final roster has been confirmed.\n\n**Selected Players:**\n{mentions}",
                color=discord.Color.red(),
            )
        )

        await channel.edit(overwrites=overwrites)

        await interaction.followup.send("Roster locked successfully!", ephemeral=True)
        self.stop()

    async def _cancel(self, interaction: discord.Interaction) -> None:
        panel = self.panel
        await interaction.response.edit_message(embed=panel.build_embed(), view=panel)
        self.stop()
