from __future__ import annotations

import asyncio
import discord
from discord.ext import commands

import config
import database as db
from data.game_data import get_countries
from views.setup_views import SetupWizard
from views.register_view import MatchChannelView, RegisterMatchView, _update_roster_embed, update_channel_panel, _ACTIONS_TEXT
from views.roster_view import RosterPanel
from views.field_report_view import FieldReportWizard

DOCTRINE_EMOJI = {"Western": "🟦", "Eastern": "🟥", "European": "🟨"}

_ROMAN: dict[str, int] = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5,
    "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
}
_INT_TO_ROMAN: dict[int, str] = {v: k for k, v in _ROMAN.items()}


def _find_latest_category(guild: discord.Guild, base_name: str) -> discord.CategoryChannel | None:
    """Return the highest-numbered category whose name starts with base_name.

    Handles suffixes like '', ' II', ' III', ' IV' (Roman numerals up to X).
    Falls back to Discord position order if the suffix isn't a recognised numeral.
    """
    base_lower = base_name.lower()
    matches = [c for c in guild.categories if c.name.lower().startswith(base_lower)]
    if not matches:
        return None

    def _rank(cat: discord.CategoryChannel) -> int:
        suffix = cat.name[len(base_name):].strip()
        if not suffix:
            return 1
        return _ROMAN.get(suffix.upper(), cat.position)

    return max(matches, key=_rank)


async def _get_archive_category(
    guild: discord.Guild, base_name: str
) -> discord.CategoryChannel:
    """Return an archive category with room (<50 channels), creating the next Roman-numeral one if the latest is full."""
    latest = _find_latest_category(guild, base_name)
    if not latest:
        return await guild.create_category(base_name)
    if len(latest.channels) < 50:
        return latest
    suffix = latest.name[len(base_name):].strip()
    current_num = _ROMAN.get(suffix.upper(), 1) if suffix else 1
    next_suffix = _INT_TO_ROMAN.get(current_num + 1, str(current_num + 1))
    return await guild.create_category(f"{base_name} {next_suffix}")


class MatchCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    def _has_rank(self, member: discord.Member) -> bool:
        return any(r.name in config.ALLOWED_RANKS for r in member.roles)

    def _is_admin(self, member: discord.Member) -> bool:
        return any(r.name in config.ADMIN_ROLES for r in member.roles)

    # ── /creategame ───────────────────────────────────────────────────────────

    @discord.slash_command(
        name="creategame",
        description="Start a new map match (Corporal+ only)",
    )
    async def creategame(self, ctx: discord.ApplicationContext) -> None:
        await ctx.defer(ephemeral=True)
        if not self._has_rank(ctx.author):
            await ctx.followup.send(
                f"You need at least **{config.ALLOWED_RANKS[0]}** rank to create a match.",
                ephemeral=True,
            )
            return

        wizard = SetupWizard(ctx.author, self._wizard_confirmed)
        embed = discord.Embed(
            title="🗺️ New Match Setup",
            description="Choose a **Game Type** to begin.",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Game Type → Region → Timezone → Time → Confirm")
        await ctx.followup.send(embed=embed, view=wizard, ephemeral=True)

    async def creategame_from_interaction(self, interaction: discord.Interaction) -> None:
        """Entry point for the hub Create Match button (replaces /creategame in the hub flow)."""
        wizard = SetupWizard(interaction.user, self._wizard_confirmed)
        embed = discord.Embed(
            title="🗺️ New Match Setup",
            description="Choose a **Game Type** to begin.",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Game Type → Region → Timezone → Time → Confirm")
        await interaction.response.send_message(embed=embed, view=wizard, ephemeral=True)

    async def _wizard_confirmed(
        self, interaction: discord.Interaction, wizard: SetupWizard
    ) -> None:
        # Defer immediately — channel + event creation easily exceeds 3 s
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild

        category = discord.utils.get(guild.categories, name=config.PREGAME_CATEGORY)
        if not category:
            category = await guild.create_category(config.PREGAME_CATEGORY)

        safe_leader = (
            interaction.user.display_name
            .lower()
            .replace(" ", "-")
            .replace(".", "")
        )
        safe_name = (
            f"{wizard.game_type}-{wizard.region}-{safe_leader}"
            .lower()
            .replace(" ", "-")
            .replace(".", "")
        )
        # Build permission overwrites: everyone can read/write but only the
        # leader (and admin roles) can use application commands in this channel.
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                use_application_commands=False,
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                manage_messages=True,
                use_application_commands=True,
            ),
            interaction.user: discord.PermissionOverwrite(
                use_application_commands=True,
            ),
        }
        for role in guild.roles:
            if role.name in config.ADMIN_ROLES:
                overwrites[role] = discord.PermissionOverwrite(
                    use_application_commands=True,
                )

        channel = await guild.create_text_channel(
            name=safe_name,
            category=category,
            topic=(
                f"{wizard.game_type}  |  {wizard.region}  "
                f"|  Led by {interaction.user.display_name}"
            ),
            overwrites=overwrites,
        )

        match_id = await db.create_game(
            channel.id, guild.id, interaction.user.id,
            wizard.game_type, wizard.region,
        )

        countries = get_countries(wizard.game_type, wizard.region)
        roster_embed = self._build_roster_embed(wizard, interaction.user, countries)
        view = MatchChannelView(channel.id, "open")
        msg = await channel.send(embed=roster_embed, view=view)
        await msg.pin()
        self.bot.add_view(view)
        await db.set_roster_message_id(match_id, msg.id)

        # Discord scheduled event using the times the leader picked in the wizard.
        country_lines = "\n".join(
            f"{DOCTRINE_EMOJI.get(c['doctrine'], '⬜')} {c['name']} ({c['doctrine']})"
            for c in countries
        ) or "*(no region data yet)*"
        event_description = (
            f"{wizard.game_type} · {wizard.region}\n"
            f"Led by {interaction.user.display_name}\n\n"
            f"{country_lines}"
        )
        try:
            event = await guild.create_scheduled_event(
                name=f"{wizard.game_type} — {wizard.region}",
                description=event_description,
                start_time=wizard.start_time,
                end_time=wizard.end_time,
                location=channel.name,
            )
            await db.set_event_id(match_id, event.id)
        except discord.HTTPException:
            pass

        # Optional announcement
        ann_ch = discord.utils.get(guild.text_channels, name=config.NEW_MAP_CHANNEL)
        if ann_ch:
            await ann_ch.send(
                embed=discord.Embed(
                    title="🗺️  New Match Starting!",
                    description=(
                        f"**{wizard.game_type}**  ·  {wizard.region}\n"
                        f"Leader: {interaction.user.mention}\n\n"
                        f"Head to {channel.mention} to register!"
                    ),
                    color=discord.Color.green(),
                )
            )

        await interaction.edit_original_response(
            embed=discord.Embed(
                title="✅  Match created!",
                description=f"Channel: {channel.mention}",
                color=discord.Color.green(),
            ),
            view=None,
        )


    # ── shared action helpers (called by both slash commands and hub buttons) ──

    async def do_cancel_match(self, match: dict, guild: discord.Guild) -> None:
        """Cancel a match: update DB, delete the Discord event, refresh hub, delete channel."""
        await db.update_match_status(match["id"], "cancelled")

        event_id = match.get("event_id")
        if event_id:
            try:
                event = await guild.fetch_scheduled_event(event_id)
                await event.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        channel = guild.get_channel(match["channel_id"])
        if channel:
            await asyncio.sleep(5)
            await channel.delete(reason="Match cancelled by leader/admin")

    async def do_start_game(
        self, interaction: discord.Interaction, match: dict, code: str
    ) -> None:
        """Core /startgame logic — usable from slash command or hub modal."""
        guild   = interaction.guild
        channel = guild.get_channel(match["channel_id"])
        leader  = guild.get_member(match["leader_id"])
        leader_name = leader.display_name if leader else str(match["leader_id"])
        safe_leader = leader_name.lower().replace(" ", "-").replace(".", "")
        new_name    = f"{code.lower()}-{safe_leader}"

        scale           = config.GAME_TYPE_SCALE.get(match["game_type"], "1X")
        active_cat_name = config.SCALE_CATEGORIES.get(scale, f"{scale} GAMES")
        active_category = discord.utils.get(guild.categories, name=active_cat_name)
        if not active_category:
            active_category = await guild.create_category(active_cat_name)

        await db.set_game_code(match["id"], code)
        if channel:
            await channel.edit(name=new_name, category=active_category)
            msg = await channel.send(
                embed=discord.Embed(
                    title="🎮  Game Found!",
                    description=f"**Game Code:** `{code}`\n\nGood luck everyone!",
                    color=discord.Color.green(),
                )
            )
            await msg.pin()

        if channel:
            match_refreshed = await db.get_match_by_channel(match["channel_id"])
            if match_refreshed:
                await update_channel_panel(match_refreshed, channel, interaction.client, "started")

        if not interaction.response.is_done():
            await interaction.response.send_message("Game started!", ephemeral=True)
        else:
            await interaction.followup.send("Game started!", ephemeral=True)

    async def do_end_game(
        self, interaction: discord.Interaction, match: dict, result: str, quiet: bool = False
    ) -> None:
        """Core /endgame logic — usable from slash command, hub button, or field report wizard."""
        if result == "Won":
            archive_name = config.VICTORY_CATEGORY
            new_status   = "won"
            embed = discord.Embed(
                title="🏆  Victory!",
                description="This game has been won. Well played!",
                color=discord.Color.gold(),
            )
        else:
            archive_name = config.LOSS_CATEGORY
            new_status   = "lost"
            embed = discord.Embed(
                title="💀  Defeat",
                description="This game has been lost. Better luck next time.",
                color=discord.Color.dark_red(),
            )

        archive_category = await _get_archive_category(interaction.guild, archive_name)

        await db.update_match_status(match["id"], new_status)
        channel = interaction.guild.get_channel(match["channel_id"])
        if channel:
            await update_channel_panel(match, channel, interaction.client, new_status)
            await channel.edit(category=archive_category)
            await channel.send(embed=embed)

        if not quiet:
            result_text = f"Game declared as **{result}**. Channel moved to **{archive_category.name}**."
            if not interaction.response.is_done():
                await interaction.response.send_message(result_text, ephemeral=True)
            else:
                await interaction.followup.send(result_text, ephemeral=True)

    async def do_unlock_roster(
        self, interaction: discord.Interaction, match: dict
    ) -> None:
        """Core /unlockroster logic — usable from slash command or hub button."""
        guild   = interaction.guild
        channel = guild.get_channel(match["channel_id"])

        await db.update_match_status(match["id"], "open")
        await db.reopen_match_registrations(match["id"])

        if channel:
            await update_channel_panel(match, channel, self.bot, "open")

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    read_messages=True, send_messages=True, use_application_commands=False,
                ),
                guild.me: discord.PermissionOverwrite(
                    read_messages=True, send_messages=True,
                    manage_messages=True, use_application_commands=True,
                ),
                interaction.user: discord.PermissionOverwrite(use_application_commands=True),
            }
            for role in guild.roles:
                if role.name in config.ADMIN_ROLES:
                    overwrites[role] = discord.PermissionOverwrite(use_application_commands=True)
            await channel.edit(overwrites=overwrites)

            await channel.send(
                embed=discord.Embed(
                    title="🔓  Roster Unlocked",
                    description="Registration is open again.\nPlayers can register using the button above.",
                    color=discord.Color.green(),
                )
            )

        if not interaction.response.is_done():
            await interaction.response.send_message("Roster unlocked.", ephemeral=True)
        else:
            await interaction.followup.send("Roster unlocked.", ephemeral=True)

    # ── roster embed builder ──────────────────────────────────────────────────

    def _build_roster_embed(
        self,
        wizard: SetupWizard,
        leader: discord.Member,
        countries: list[dict],
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"🗺️  {wizard.game_type}  —  {wizard.region}",
            description=f"**Leader:** {leader.mention}",
            color=discord.Color.blue(),
        )

        if countries:
            lines = [
                f"{DOCTRINE_EMOJI.get(c['doctrine'], '⬜')} **{c['name']}**  ·  "
                f"{c['doctrine']}  ·  {c['cities']} cities"
                for c in countries
            ]
            embed.add_field(name="Available Countries", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Available Countries", value="*(data not yet loaded)*", inline=False)

        embed.add_field(
            name="Military Roles  (one each)",
            value="  ·  ".join(config.MILITARY_ROLES),
            inline=False,
        )
        embed.add_field(
            name="Squad Roles",
            value=(
                "1× Scout  ·  1× Spy *(optional)*  ·  ∞ Soldiers\n"
                "-# Spy may pick from any country in the full game type map."
            ),
            inline=False,
        )
        embed.add_field(
            name="Available Actions",
            value=_ACTIONS_TEXT["open"],
            inline=False,
        )
        embed.set_footer(text="Click Register below to join.")
        return embed

    # ── /roster ───────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="roster",
        description="Open the roster management panel (Map Leader / Admin only)",
    )
    async def roster(self, ctx: discord.ApplicationContext) -> None:
        await ctx.defer(ephemeral=True)
        match = await db.get_match_by_channel(ctx.channel_id)
        if not match:
            await ctx.followup.send("This isn't a match channel.", ephemeral=True)
            return

        if match["leader_id"] != ctx.author.id and not self._is_admin(ctx.author):
            await ctx.followup.send("Only the Map Leader or an Admin can open the roster panel.", ephemeral=True)
            return

        regs = await db.get_registrations(match["id"])
        members = {r["user_id"]: ctx.guild.get_member(r["user_id"]) for r in regs}

        panel = RosterPanel(match, regs, members)
        await ctx.followup.send(embed=panel.build_embed(), view=panel, ephemeral=True)

    # ── /unlockroster ─────────────────────────────────────────────────────────

    @discord.slash_command(
        name="unlockroster",
        description="Reopen registration after a roster lock (Map Leader only)",
    )
    async def unlockroster(self, ctx: discord.ApplicationContext) -> None:
        await ctx.defer(ephemeral=True)
        match = await db.get_match_by_channel(ctx.channel_id)
        if not match:
            await ctx.followup.send("This isn't a match channel.", ephemeral=True)
            return
        if match["leader_id"] != ctx.author.id:
            await ctx.followup.send("Only the Map Leader can unlock the roster.", ephemeral=True)
            return
        if match["status"] != "locked":
            status_msgs = {
                "open":    "The roster isn't locked.",
                "started": "The game is already in progress — the roster can't be unlocked.",
                "won":     "This game has already ended.",
                "lost":    "This game has already ended.",
            }
            await ctx.followup.send(
                status_msgs.get(match["status"], "This match can't be unlocked."), ephemeral=True
            )
            return

        await self.do_unlock_roster(ctx.interaction, match)

    # ── /startgame ────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="startgame",
        description="Enter the game code once a lobby is found (Map Leader / Admin only)",
    )
    async def startgame(
        self,
        ctx: discord.ApplicationContext,
        code: str = discord.Option(description="Game lobby code (8 digits)"),
    ) -> None:
        await ctx.defer(ephemeral=True)
        match = await db.get_match_by_channel(ctx.channel_id)
        if not match:
            await ctx.followup.send("This isn't a match channel.", ephemeral=True)
            return
        if match["leader_id"] != ctx.author.id and not self._is_admin(ctx.author):
            await ctx.followup.send("Only the Map Leader or an Admin can start the game.", ephemeral=True)
            return
        if match["status"] != "locked":
            await ctx.followup.send("The roster must be locked before starting the game.", ephemeral=True)
            return

        if not (code.isdigit() and len(code) == 8):
            await ctx.followup.send("Game code must be exactly **8 digits**.", ephemeral=True)
            return

        await self.do_start_game(ctx.interaction, match, code)

    # ── /endgame ──────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="endgame",
        description="Declare the game outcome and archive the channel (Map Leader / Admin only)",
    )
    async def endgame(
        self,
        ctx: discord.ApplicationContext,
        result: str = discord.Option(description="Game outcome", choices=["Won", "Lost"]),
    ) -> None:
        match = await db.get_match_by_channel(ctx.channel_id)
        if not match:
            await ctx.respond("This isn't a match channel.", ephemeral=True)
            return
        if match["leader_id"] != ctx.author.id and not self._is_admin(ctx.author):
            await ctx.respond(
                "Only the Map Leader or an Admin can declare the game outcome.", ephemeral=True
            )
            return
        if match["status"] != "started":
            await ctx.respond(
                "The game must be in progress before declaring an outcome. "
                "Use the **Start Game** button in the match channel first.",
                ephemeral=True,
            )
            return

        regs = await db.get_registrations(match["id"])
        players = [r for r in regs if r["status"] == "selected"] or regs
        if not players:
            await self.do_end_game(ctx.interaction, match, result)
            return

        members = {r["user_id"]: ctx.guild.get_member(r["user_id"]) for r in players}

        async def on_complete(post_interaction: discord.Interaction) -> None:
            await self.do_end_game(post_interaction, match, result, quiet=True)

        wizard = FieldReportWizard(match, players, members, ctx.interaction, on_complete=on_complete)
        await ctx.respond(embed=wizard.build_embed(), view=wizard, ephemeral=True)

    # ── /fieldreport ──────────────────────────────────────────────────────────

    @discord.slash_command(
        name="fieldreport",
        description="File a post-game field report for all players (Map Leader / Admin only)",
    )
    async def fieldreport(self, ctx: discord.ApplicationContext) -> None:
        match = await db.get_match_by_channel(ctx.channel_id)
        if not match:
            await ctx.respond("This isn't a match channel.", ephemeral=True)
            return
        if match["leader_id"] != ctx.author.id and not self._is_admin(ctx.author):
            await ctx.respond(
                "Only the Map Leader or an Admin can file a field report.", ephemeral=True
            )
            return
        if match["status"] not in ("won", "lost"):
            await ctx.respond(
                "Field reports can only be filed after the game has ended (Won or Lost).",
                ephemeral=True,
            )
            return

        regs = await db.get_registrations(match["id"])
        players = [r for r in regs if r["status"] == "selected"] or regs
        if not players:
            await ctx.respond("No registered players found for this match.", ephemeral=True)
            return

        members = {r["user_id"]: ctx.guild.get_member(r["user_id"]) for r in players}
        wizard = FieldReportWizard(match, players, members, ctx.interaction)
        await ctx.respond(embed=wizard.build_embed(), view=wizard, ephemeral=True)

    # ── /cancelgame ───────────────────────────────────────────────────────────

    @discord.slash_command(
        name="cancelgame",
        description="Cancel this game and delete the channel",
    )
    async def cancelgame(self, ctx: discord.ApplicationContext) -> None:
        await ctx.defer(ephemeral=True)
        match = await db.get_match_by_channel(ctx.channel_id)
        if not match:
            await ctx.followup.send("This isn't a match channel.", ephemeral=True)
            return

        if match["leader_id"] != ctx.author.id and not self._is_admin(ctx.author):
            await ctx.followup.send("Only the Map Leader or an Admin can cancel this game.", ephemeral=True)
            return

        if match["status"] == "started":
            await ctx.followup.send(
                "The game is already in progress — use the **End Game** option in the match channel instead.",
                ephemeral=True,
            )
            return
        if match["status"] in ("won", "lost"):
            await ctx.followup.send(
                "This game has already ended and cannot be cancelled.", ephemeral=True
            )
            return

        await ctx.followup.send(
            "Cancel this game and **delete the channel**?",
            view=_CancelConfirmView(match, ctx.interaction.client),
            ephemeral=True,
        )

    # ── /withdraw ─────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="withdraw",
        description="Withdraw your registration from this match",
    )
    async def withdraw(self, ctx: discord.ApplicationContext) -> None:
        await ctx.defer(ephemeral=True)
        match = await db.get_match_by_channel(ctx.channel_id)
        if not match:
            await ctx.followup.send("This isn't a match channel.", ephemeral=True)
            return
        if match["leader_id"] == ctx.author.id:
            await ctx.followup.send(
                "As the Match Leader you cannot withdraw — use `/cancelgame` to cancel the match.",
                ephemeral=True,
            )
            return
        if match["status"] != "open":
            if match["status"] in ("won", "lost"):
                msg = "This game has already ended — withdrawal is not possible."
            elif match["status"] == "started":
                msg = "This game is already in progress — withdrawal is not possible."
            else:
                msg = "The roster is locked — withdrawal is not possible."
            await ctx.followup.send(msg, ephemeral=True)
            return

        reg = await db.get_registration(match["id"], ctx.author.id)
        if not reg:
            await ctx.followup.send("You aren't registered for this match.", ephemeral=True)
            return

        await db.withdraw_registration(reg["id"])
        await ctx.followup.send("You've withdrawn from this match.", ephemeral=True)
        await _update_roster_embed(match, ctx.channel)

    # ── /help ─────────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="help",
        description="Show how to use CON Game Pilot",
    )
    async def help(self, ctx: discord.ApplicationContext) -> None:
        embed = discord.Embed(
            title="📖  How to Use CON Game Pilot",
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="🗺️  Creating a Match",
            value=(
                f"Go to **#match-hub** and click **🗺️ Create Match** (requires **{config.ALLOWED_RANKS[0]}** rank or above).\n"
                "A setup wizard will guide you through game type, region, and start time."
            ),
            inline=False,
        )
        embed.add_field(
            name="📋  Registering",
            value=(
                "Inside the match channel, click **📋 Register** on the pinned message.\n"
                "Pick your Squad Role, Military Role, and country, then confirm.\n"
                "Use the **🚪 Withdraw** button on your registration card to opt out."
            ),
            inline=False,
        )
        embed.add_field(
            name="🔒  Locking the Roster  *(Map Leader / Admin)*",
            value=(
                "Click **🔒 Lock Roster** → select which players to keep → confirm.\n"
                "The channel becomes private to selected players only.\n"
                "Click **🔓 Unlock Roster** to reopen registration if needed."
            ),
            inline=False,
        )
        embed.add_field(
            name="🎮  Starting the Game  *(Map Leader / Admin)*",
            value=(
                "After locking, click **🎮 Start Game** and enter the 8-digit lobby code.\n"
                "The channel is renamed to the code and moved to the active games category."
            ),
            inline=False,
        )
        embed.add_field(
            name="📅  Editing the Schedule  *(Map Leader / Admin)*",
            value="Click **📅 Edit Schedule** on the pinned message to update the Discord event's start time.",
            inline=False,
        )
        embed.add_field(
            name="❌  Cancelling  *(Map Leader only)*",
            value="Click **❌ Cancel Match** on the pinned message. The channel and Discord event are deleted after 5 seconds.",
            inline=False,
        )
        embed.add_field(
            name="📝  Field Report  *(Map Leader / Admin)*",
            value=(
                "After the game ends, run `/fieldreport` in the match channel.\n"
                "Score each player and add overall comments — the bot formats and posts the full report."
            ),
            inline=False,
        )
        embed.add_field(
            name="ℹ️  Notes",
            value=(
                "• Spy squad role allows picking any country across the full game map\n"
                "• Leader and Admin roles always keep channel access after roster lock"
            ),
            inline=False,
        )
        embed.set_footer(text="CON Game Pilot • Map Match Manager")
        await ctx.respond(embed=embed, ephemeral=True)


# ── cancel-match confirm view ─────────────────────────────────────────────────

class _CancelConfirmView(discord.ui.View):
    def __init__(self, match: dict, bot: discord.Bot | None = None):
        super().__init__(timeout=60)
        self.match = match
        self._bot  = bot

    @discord.ui.button(label="Yes, Cancel Match", style=discord.ButtonStyle.danger)
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Match cancelled. Channel deletes in 5 seconds…", ephemeral=True
        )
        self.stop()
        cog = interaction.client.cogs.get("MatchCog")
        if cog:
            await cog.do_cancel_match(self.match, interaction.guild)
        else:
            await db.update_match_status(self.match["id"], "cancelled")
            await asyncio.sleep(5)
            await interaction.channel.delete(reason="Match cancelled by leader/admin")

    @discord.ui.button(label="Keep Match", style=discord.ButtonStyle.secondary)
    async def keep(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="Cancellation aborted.", view=None)
        self.stop()


def setup(bot: discord.Bot) -> None:
    bot.add_cog(MatchCog(bot))
