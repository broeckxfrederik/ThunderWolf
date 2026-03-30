"""
Shared Discord utilities used across multiple cogs.
"""

import discord
import db


def resolve_role(
    guild: discord.Guild,
    cfg_key: str,
    fallback_name: str,
) -> discord.Role | None:
    """
    Look up a Discord role by DB-configured ID first, then by name.
    Used by greeting, roles, and race_event cogs.
    """
    raw_id = db.get_config(guild.id, cfg_key)
    if raw_id:
        role = guild.get_role(int(raw_id))
        if role:
            return role
    return discord.utils.get(guild.roles, name=fallback_name)
