"""
Setup cog
─────────
• /setup  — one-time server setup command (guild owner only).

  What it creates (skips anything that already exists):

  Roles
  ─────
  CEO, Team-Manager, Racer, Visitor, Updates-Only,
  Livery-Prodigy, F1-Updates, Twitch-Notifications, Driver-Notification

  Channels / Categories
  ─────────────────────
  • "New Members" category   — visible to owner + CEO only
  • #team-manager-lineups    — visible to CEO + Team-Manager only

  After running, the bot reports exactly what was created vs already present.
"""

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    ROLE_CEO, ROLE_TEAM_MANAGER,
    ROLE_RACER, ROLE_VISITOR, ROLE_UPDATES,
    ROLE_LIVERY, ROLE_F1, ROLE_TWITCH, ROLE_DRIVER,
    WELCOME_CATEGORY, CHANNEL_LINEUP,
)

# Ordered so that higher-authority roles are created first (Discord shows
# newly-created roles below the bot's own role, so order here is cosmetic).
ALL_ROLES = [
    ROLE_CEO,
    ROLE_TEAM_MANAGER,
    ROLE_RACER,
    ROLE_VISITOR,
    ROLE_UPDATES,
    ROLE_LIVERY,
    ROLE_F1,
    ROLE_TWITCH,
    ROLE_DRIVER,
]


def _is_owner():
    """App-command check: only the guild owner may run /setup."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False
        return interaction.user.id == interaction.guild.owner_id
    return app_commands.check(predicate)


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="setup",
        description="One-time server setup: creates all required roles and channels (owner only).",
    )
    @_is_owner()
    async def setup(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        created: list[str] = []
        skipped: list[str] = []

        # ── 1. Roles ──────────────────────────────────────────────────────────
        for role_name in ALL_ROLES:
            if discord.utils.get(guild.roles, name=role_name):
                skipped.append(f"role `{role_name}`")
            else:
                await guild.create_role(name=role_name, reason="/setup command")
                created.append(f"role `{role_name}`")

        # Fetch fresh role objects after creation
        ceo_role = discord.utils.get(guild.roles, name=ROLE_CEO)
        tm_role  = discord.utils.get(guild.roles, name=ROLE_TEAM_MANAGER)

        # ── 2. "New Members" category ─────────────────────────────────────────
        if discord.utils.get(guild.categories, name=WELCOME_CATEGORY):
            skipped.append(f"category `{WELCOME_CATEGORY}`")
        else:
            overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
            }
            if guild.owner:
                overwrites[guild.owner] = discord.PermissionOverwrite(view_channel=True)
            if ceo_role:
                overwrites[ceo_role] = discord.PermissionOverwrite(view_channel=True)

            await guild.create_category(
                name=WELCOME_CATEGORY,
                overwrites=overwrites,
                reason="/setup command",
            )
            created.append(f"category `{WELCOME_CATEGORY}`")

        # ── 3. #team-manager-lineups ──────────────────────────────────────────
        if discord.utils.get(guild.text_channels, name=CHANNEL_LINEUP):
            skipped.append(f"channel `#{CHANNEL_LINEUP}`")
        else:
            ch_overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
            }
            if ceo_role:
                ch_overwrites[ceo_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
            if tm_role:
                ch_overwrites[tm_role]  = discord.PermissionOverwrite(view_channel=True, send_messages=True)

            await guild.create_text_channel(
                name=CHANNEL_LINEUP,
                overwrites=ch_overwrites,
                reason="/setup command",
            )
            created.append(f"channel `#{CHANNEL_LINEUP}`")

        # ── 4. Reply ──────────────────────────────────────────────────────────
        lines: list[str] = ["## ✅ ThunderWolf Setup Complete\n"]

        if created:
            lines.append("**Created:**")
            lines.extend(f"  • {item}" for item in created)
        if skipped:
            lines.append("\n**Already existed (skipped):**")
            lines.extend(f"  • {item}" for item in skipped)

        lines.append(
            "\n> Make sure the bot's role sits **above** all the roles listed above "
            "in Server Settings → Roles so it can assign them."
        )

        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @setup.error
    async def setup_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                "❌ Only the server owner can run `/setup`.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))
