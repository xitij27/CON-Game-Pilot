"""Shared utilities for the match-hub channel."""
from __future__ import annotations

import discord

import config


async def ensure_hub_channel(guild: discord.Guild) -> discord.TextChannel:
    """Return (creating if needed) the match-hub channel in the game centre category."""
    category = discord.utils.get(guild.categories, name=config.CATEGORY_NAME)
    if not category:
        category = await guild.create_category(config.CATEGORY_NAME)

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
