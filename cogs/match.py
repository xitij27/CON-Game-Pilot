from __future__ import annotations

import asyncio
import discord
from discord.ext import commands

import config
import database as db
from data.game_data import get_countries
from views.setup_views import SetupWizard
from views.register_view import RegisterMatchView, _update_roster_embed
from views.roster_view import RosterPanel
from hub_utils import refresh_hub_card, build_match_card_embed

DOCTRINE_EMOJI = {"Western": "🟦", "Eastern": "🟥", "European": "🟨"}


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
        guild = interaction.guild

        category = discord.utils.get(guild.categories, name=config.PREGAME_CATEGORY)
        if not category:
            category = await guild.create_category(config.PREGAME_CATEGORY)

        safe_name = (
            f"{wizard.game_type}-{wizard.region}"
            .lower()
            .replace(" ", "-")
            .replace(".", "")
        )
        # Build permission overwrites: everyone can read/write but only the
        # leader (and admin roles) can use slash commands in this channel.
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
        view = RegisterMatchView(channel.id)
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
            await guild.create_scheduled_event(
                name=f"{wizard.game_type} — {wizard.region}",
                description=event_description,
                start_time=wizard.start_time,
                end_time=wizard.end_time,
                location=channel.name,
            )
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

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="✅  Match created!",
                description=f"Channel: {channel.mention}",
                color=discord.Color.green(),
            ),
            view=None,
        )

        # Post a match card to the hub channel
        import config as _cfg
        hub_channel = discord.utils.get(guild.text_channels, name=_cfg.MATCH_HUB_CHANNEL)
        if hub_channel:
            match_row = await db.get_match_by_channel(channel.id)
            if match_row:
                from views.hub_view import MatchCardView
                hub_embed = await build_match_card_embed(match_row, guild)
                hub_view  = MatchCardView(channel.id)
                hub_msg   = await hub_channel.send(embed=hub_embed, view=hub_view)
                self.bot.add_view(hub_view)
                await db.set_hub_message_id(match_id, hub_msg.id)

    # ── shared action helpers (called by both slash commands and hub buttons) ──

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

        match_refreshed = await db.get_match_by_channel(match["channel_id"])
        if match_refreshed:
            await refresh_hub_card(interaction.client, match_refreshed)

        if not interaction.response.is_done():
            await interaction.response.send_message("Game started!", ephemeral=True)
        else:
            await interaction.followup.send("Game started!", ephemeral=True)

    async def do_end_game(
        self, interaction: discord.Interaction, match: dict, result: str
    ) -> None:
        """Core /endgame logic — usable from slash command or hub button."""
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

        archive_category = discord.utils.get(interaction.guild.categories, name=archive_name)
        if not archive_category:
            archive_category = await interaction.guild.create_category(archive_name)

        await db.update_match_status(match["id"], new_status)
        channel = interaction.guild.get_channel(match["channel_id"])
        if channel:
            await channel.edit(category=archive_category)
            await channel.send(embed=embed)

        match_refreshed = await db.get_match_by_channel(match["channel_id"])
        if match_refreshed:
            await refresh_hub_card(interaction.client, match_refreshed)

        result_text = f"Game declared as **{result}**. Channel moved to **{archive_name}**."
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
            roster_msg_id = match.get("roster_message_id")
            if roster_msg_id:
                try:
                    roster_msg = await channel.fetch_message(roster_msg_id)
                    view = RegisterMatchView(channel.id)
                    await roster_msg.edit(view=view)
                    self.bot.add_view(view)
                except (discord.NotFound, discord.Forbidden):
                    pass

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

        match_refreshed = await db.get_match_by_channel(match["channel_id"])
        if match_refreshed:
            await refresh_hub_card(interaction.client, match_refreshed)

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
        await ctx.defer(ephemeral=True)
        match = await db.get_match_by_channel(ctx.channel_id)
        if not match:
            await ctx.followup.send("This isn't a match channel.", ephemeral=True)
            return
        if match["leader_id"] != ctx.author.id and not self._is_admin(ctx.author):
            await ctx.followup.send(
                "Only the Map Leader or an Admin can declare the game outcome.", ephemeral=True
            )
            return
        if match["status"] != "started":
            await ctx.followup.send(
                "The game must be in progress before declaring an outcome. "
                "Use `/startgame` first.",
                ephemeral=True,
            )
            return

        await self.do_end_game(ctx.interaction, match, result)

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
                "The game is already in progress — use `/endgame` to declare the outcome instead.",
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

        match_refreshed = await db.get_match_by_channel(ctx.channel_id)
        if match_refreshed:
            await refresh_hub_card(self.bot, match_refreshed)

    # ── /help ─────────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="help",
        description="Show all available CommandPost commands",
    )
    async def help(self, ctx: discord.ApplicationContext) -> None:
        embed = discord.Embed(
            title="📖  CommandPost Commands",
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="🌐  Anyone",
            value=(
                "`/creategame` — Launch the match setup wizard and open registration\n"
                "`/withdraw` — Withdraw your registration from the current match\n"
                "📋 **Register** — Click the Register button in the match channel to join"
            ),
            inline=False,
        )
        embed.add_field(
            name="🗺️  Map Leader & Admins",
            value=(
                "`/roster` — Open the roster panel to select players and lock the roster\n"
                "`/startgame <code>` — Enter the 8-digit lobby code; moves channel to active category\n"
                "`/endgame <Won|Lost>` — Declare the outcome; moves channel to Victory Wall or Loss Log\n"
                "`/cancelgame` — Cancel the game and delete the channel *(pre-start only)*"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔓  Map Leader only",
            value="`/unlockroster` — Reopen registration after a roster lock; resets all selections",
            inline=False,
        )
        embed.add_field(
            name="ℹ️  Notes",
            value=(
                f"• `/creategame` requires **{config.ALLOWED_RANKS[0]}** rank or above\n"
                "• Leader and Admin roles always keep channel access after roster lock\n"
                "• Spy squad role allows picking any country across the full game map"
            ),
            inline=False,
        )
        embed.set_footer(text="CommandPost • Map Match Manager")
        await ctx.respond(embed=embed, ephemeral=True)


# ── cancel-match confirm view ─────────────────────────────────────────────────

class _CancelConfirmView(discord.ui.View):
    def __init__(self, match: dict, bot: discord.Bot | None = None):
        super().__init__(timeout=60)
        self.match = match
        self._bot  = bot

    @discord.ui.button(label="Yes, Cancel Match", style=discord.ButtonStyle.danger)
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        await db.update_match_status(self.match["id"], "cancelled")
        channel = interaction.channel
        await interaction.response.send_message(
            "Match cancelled. Channel deletes in 5 seconds…", ephemeral=True
        )
        self.stop()

        bot = self._bot or interaction.client
        match_refreshed = await db.get_match_by_channel(self.match["channel_id"])
        if match_refreshed:
            await refresh_hub_card(bot, match_refreshed)

        await asyncio.sleep(5)
        await channel.delete(reason="Match cancelled by leader/admin")

    @discord.ui.button(label="Keep Match", style=discord.ButtonStyle.secondary)
    async def keep(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="Cancellation aborted.", view=None)
        self.stop()


def setup(bot: discord.Bot) -> None:
    bot.add_cog(MatchCog(bot))
