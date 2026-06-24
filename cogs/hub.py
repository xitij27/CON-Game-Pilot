"""HubCog — sets up the match-hub channel and posts the control panel on startup."""
from __future__ import annotations

import discord
from discord.ext import commands

import database as db
from hub_utils import ensure_hub_channel
from views.hub_view import MatchHubControlView

_CONTROL_PANEL_KEY = "hub_control_message_id"


class HubCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    async def setup_hub(self, guild: discord.Guild) -> None:
        hub_channel = await ensure_hub_channel(guild)

        control_view = MatchHubControlView()
        embed = discord.Embed(
            title="🗺️  Match Hub",
            description=(
                "Welcome to the Match Hub!\n\n"
                "Use **Create Match** to open a new map match."
            ),
            color=discord.Color.blurple(),
        )
        control_msg = await hub_channel.send(embed=embed, view=control_view)
        await control_msg.pin()
        await db.set_setting(_CONTROL_PANEL_KEY, str(control_msg.id))
        self.bot.add_view(control_view)

        print("[hub] Control panel posted.", flush=True)


def setup(bot: discord.Bot) -> None:
    bot.add_cog(HubCog(bot))
