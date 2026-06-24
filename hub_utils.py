"""Shared utilities for the match-hub channel panel."""
from __future__ import annotations

import discord

import config
import database as db

_STATUS_EMOJI = {
    "open":      "🟢",
    "locked":    "🔒",
    "started":   "🎮",
    "won":       "🏆",
    "lost":      "💀",
    "cancelled": "❌",
}

_STATUS_LABEL = {
    "open":      "Open — accepting registrations",
    "locked":    "Roster locked",
    "started":   "In progress",
    "won":       "Ended — Victory",
    "lost":      "Ended — Defeat",
    "cancelled": "Cancelled",
}


async def build_match_card_embed(match: dict, guild: discord.Guild) -> discord.Embed:
    """Build the hub embed for a single match."""
    status = match["status"]
    emoji  = _STATUS_EMOJI.get(status, "❓")
    label  = _STATUS_LABEL.get(status, status.title())

    leader = guild.get_member(match["leader_id"])
    leader_str = leader.mention if leader else f"<@{match['leader_id']}>"

    regs = await db.get_registrations(match["id"])

    color_map = {
        "open":      discord.Color.blue(),
        "locked":    discord.Color.orange(),
        "started":   discord.Color.green(),
        "won":       discord.Color.gold(),
        "lost":      discord.Color.dark_red(),
        "cancelled": discord.Color.dark_grey(),
    }

    embed = discord.Embed(
        title=f"🗺️  {match['game_type']}  —  {match['region']}",
        color=color_map.get(status, discord.Color.blurple()),
    )
    embed.add_field(name="Leader",  value=leader_str,          inline=True)
    embed.add_field(name="Status",  value=f"{emoji} {label}",  inline=True)
    embed.add_field(name="Players", value=str(len(regs)),       inline=True)

    channel = guild.get_channel(match["channel_id"])
    if channel:
        embed.add_field(name="Match Channel", value=channel.mention, inline=False)

    if match.get("game_code") and status in ("started", "won", "lost"):
        embed.add_field(name="Game Code", value=f"`{match['game_code']}`", inline=True)

    return embed


async def refresh_hub_card(bot: discord.Bot, match: dict) -> None:
    """Edit the match's hub card message to reflect current DB state."""
    hub_msg_id = match.get("hub_message_id")
    if not hub_msg_id:
        return

    guild = bot.get_guild(config.GUILD_ID)
    if not guild:
        return

    hub_channel = discord.utils.get(guild.text_channels, name=config.MATCH_HUB_CHANNEL)
    if not hub_channel:
        return

    try:
        msg = await hub_channel.fetch_message(hub_msg_id)
    except (discord.NotFound, discord.Forbidden):
        return

    embed = await build_match_card_embed(match, guild)
    await msg.edit(embed=embed, view=None)


async def ensure_hub_channel(guild: discord.Guild) -> discord.TextChannel:
    """Return (creating if needed) the match-hub channel in the game centre category."""
    category = discord.utils.get(guild.categories, name=config.GAME_CENTRE_CATEGORY)
    if not category:
        category = await guild.create_category(config.GAME_CENTRE_CATEGORY)

    channel = discord.utils.get(guild.text_channels, name=config.MATCH_HUB_CHANNEL)
    if not channel:
        channel = await guild.create_text_channel(
            name=config.MATCH_HUB_CHANNEL,
            category=category,
            topic="Match Hub — create and manage map matches here.",
        )
    elif channel.category_id != category.id:
        await channel.edit(category=category)

    return channel
