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

        existing = await db.get_open_match_by_leader(ctx.author.id, ctx.guild_id)
        if existing:
            await ctx.followup.send(
                "You already have an open match. Use `/cancelgame` in that channel first.",
                ephemeral=True,
            )
            return

        wizard = SetupWizard(ctx.author, self._wizard_confirmed)
        embed = discord.Embed(
            title="🗺️ New Match Setup",
            description="Choose a **Game Type** to begin.",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Game Type → Region → Confirm")
        await ctx.followup.send(embed=embed, view=wizard, ephemeral=True)

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
                "1× Leader  ·  1× Scout  ·  1× Spy *(optional)*  ·  ∞ Soldiers\n"
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

        guild = ctx.guild
        channel = ctx.channel
        leader = guild.get_member(match["leader_id"])
        leader_name = (leader.display_name if leader else str(match["leader_id"]))
        safe_leader = leader_name.lower().replace(" ", "-").replace(".", "")
        safe_code = code.lower().replace(" ", "-")
        new_name = f"{safe_code}-{safe_leader}"

        scale = config.GAME_TYPE_SCALE.get(match["game_type"], "1X")
        active_category_name = config.SCALE_CATEGORIES.get(scale, f"{scale} GAMES")
        active_category = discord.utils.get(guild.categories, name=active_category_name)
        if not active_category:
            active_category = await guild.create_category(active_category_name)

        await db.set_game_code(match["id"], code)
        await channel.edit(name=new_name, category=active_category)
        msg = await channel.send(
            embed=discord.Embed(
                title="🎮  Game Found!",
                description=f"**Game Code:** `{code}`\n\nGood luck everyone!",
                color=discord.Color.green(),
            )
        )
        await msg.pin()
        await ctx.followup.send("Game started!", ephemeral=True)

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

        archive_category = discord.utils.get(ctx.guild.categories, name=archive_name)
        if not archive_category:
            archive_category = await ctx.guild.create_category(archive_name)

        await db.update_match_status(match["id"], new_status)
        await ctx.channel.edit(category=archive_category)
        await ctx.channel.send(embed=embed)
        await ctx.followup.send(
            f"Game declared as **{result}**. Channel moved to **{archive_name}**.", ephemeral=True
        )

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
            view=_CancelConfirmView(match),
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
        if match["status"] == "locked":
            await ctx.followup.send("The roster is locked — contact the Map Leader.", ephemeral=True)
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
    def __init__(self, match: dict):
        super().__init__(timeout=60)
        self.match = match

    @discord.ui.button(label="Yes, Cancel Match", style=discord.ButtonStyle.danger)
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        await db.update_match_status(self.match["id"], "cancelled")
        channel = interaction.channel
        await interaction.response.send_message(
            "Match cancelled. Channel deletes in 5 seconds…", ephemeral=True
        )
        self.stop()
        await asyncio.sleep(5)
        await channel.delete(reason="Match cancelled by leader/admin")

    @discord.ui.button(label="Keep Match", style=discord.ButtonStyle.secondary)
    async def keep(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="Cancellation aborted.", view=None)
        self.stop()


def setup(bot: discord.Bot) -> None:
    bot.add_cog(MatchCog(bot))
