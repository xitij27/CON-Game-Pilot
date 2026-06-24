"""HubCog — sets up the match-hub channel and restores its persistent views on startup."""
from __future__ import annotations

import discord
from discord.ext import commands

import database as db
from hub_utils import build_match_card_embed, ensure_hub_channel
from views.hub_view import MatchHubControlView, MatchCardView


_CONTROL_PANEL_KEY = "hub_control_message_id"


class HubCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    async def setup_hub(self, guild: discord.Guild) -> None:
        """Called from bot.setup_hook after DB init. Creates the hub channel and restores views."""
        hub_channel = await ensure_hub_channel(guild)

        # Restore or (re)post the control panel
        stored_id = await db.get_setting(_CONTROL_PANEL_KEY)
        control_msg = None
        if stored_id:
            try:
                control_msg = await hub_channel.fetch_message(int(stored_id))
            except (discord.NotFound, discord.Forbidden):
                control_msg = None

        control_view = MatchHubControlView()
        if control_msg is None:
            embed = discord.Embed(
                title="🗺️  Match Hub",
                description=(
                    "Welcome to the Match Hub!\n\n"
                    "Use **Create Match** to open a new map match.\n"
                    "Active matches appear below — click **Register** to join one."
                ),
                color=discord.Color.blurple(),
            )
            control_msg = await hub_channel.send(embed=embed, view=control_view)
            await control_msg.pin()
            await db.set_setting(_CONTROL_PANEL_KEY, str(control_msg.id))
        else:
            await control_msg.edit(view=control_view)

        self.bot.add_view(control_view)

        # Restore per-match card views for all non-terminal matches
        active_matches = await db.get_non_cancelled_matches()
        restored = 0
        for match in active_matches:
            if match["status"] in ("won", "lost"):
                continue
            hub_msg_id = match.get("hub_message_id")
            if not hub_msg_id:
                # Match predates the hub feature — post a card now
                await self._post_match_card(hub_channel, guild, match)
            else:
                try:
                    await hub_channel.fetch_message(hub_msg_id)
                    card_view = MatchCardView(match["channel_id"])
                    self.bot.add_view(card_view)
                    restored += 1
                except (discord.NotFound, discord.Forbidden):
                    # Message was deleted — repost
                    await self._post_match_card(hub_channel, guild, match)

        print(
            f"[hub] Control panel restored. {restored} match card view(s) restored.",
            flush=True,
        )

    async def _post_match_card(
        self,
        hub_channel: discord.TextChannel,
        guild: discord.Guild,
        match: dict,
    ) -> None:
        """Post a fresh match card to hub and save the message ID."""
        embed = await build_match_card_embed(match, guild)
        view  = MatchCardView(match["channel_id"])
        msg   = await hub_channel.send(embed=embed, view=view)
        self.bot.add_view(view)
        await db.set_hub_message_id(match["id"], msg.id)


def setup(bot: discord.Bot) -> None:
    bot.add_cog(HubCog(bot))
