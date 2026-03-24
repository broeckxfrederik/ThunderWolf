"""
Reaction Roles cog
──────────────────
• /post-roles  — posts a pinned message in the current channel with emoji
  reactions.  Clicking a reaction adds / removes the matching opt-in role.

Emoji → Role mapping is defined in config.REACTION_ROLES.
"""

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    REACTION_ROLES, ROLE_CEO, ROLE_TEAM_MANAGER,
    ROLE_DRIVER, ROLE_ENGINEER, ROLE_LIVERY, ROLE_VISITOR, ROLE_UPDATES,
)


# message_id of the active reaction-role post (in-memory)
_reaction_message_id: int | None = None


class ReactionRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── slash command ─────────────────────────────────────────────────────────

    @app_commands.command(name="post-roles", description="Post the opt-in role selection message here.")
    @app_commands.checks.has_any_role(ROLE_CEO, ROLE_TEAM_MANAGER)
    async def post_roles(self, interaction: discord.Interaction):
        global _reaction_message_id

        lines = [
            "**🎭 Server Roles**\n",
            "**Team roles** — use `/role-request` to apply:",
        ]
        team_role_lines = [
            f"🏎️ — **{ROLE_DRIVER}**",
            f"🔧 — **{ROLE_ENGINEER}**",
            f"🎨 — **{ROLE_LIVERY}**",
            f"👤 — **{ROLE_VISITOR}**",
            f"🔔 — **{ROLE_UPDATES}**",
        ]
        lines.extend(team_role_lines)

        lines.append(f"\n**Opt-in roles** — react below to add or remove:")
        for emoji, role_name in REACTION_ROLES.items():
            lines.append(f"{emoji} — **{role_name}**")

        msg = await interaction.channel.send("\n".join(lines))

        # Add the reactions so users can click them
        for emoji in REACTION_ROLES:
            await msg.add_reaction(emoji)

        try:
            await msg.pin()
        except discord.Forbidden:
            pass  # no pin permission – that's fine

        _reaction_message_id = msg.id
        await interaction.response.send_message("✅ Role selection message posted.", ephemeral=True)

    # ── reaction add ──────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.message_id != _reaction_message_id:
            return
        if payload.user_id == self.bot.user.id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        role_name = REACTION_ROLES.get(str(payload.emoji))
        if role_name is None:
            return

        role = discord.utils.get(guild.roles, name=role_name)
        member = guild.get_member(payload.user_id)
        if role and member:
            await member.add_roles(role, reason="Reaction role")

    # ── reaction remove ───────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.message_id != _reaction_message_id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        role_name = REACTION_ROLES.get(str(payload.emoji))
        if role_name is None:
            return

        role = discord.utils.get(guild.roles, name=role_name)
        member = guild.get_member(payload.user_id)
        if role and member:
            await member.remove_roles(role, reason="Reaction role removed")

    # ── error handler ─────────────────────────────────────────────────────────

    @post_roles.error
    async def post_roles_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingAnyRole):
            await interaction.response.send_message(
                "❌ Only CEO or Team Manager can post the roles message.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionRoles(bot))
