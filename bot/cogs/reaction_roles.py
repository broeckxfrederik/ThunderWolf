"""
Reaction Roles cog
──────────────────
• /post-roles  — posts a pinned message in the current channel with buttons.
  Clicking a button toggles the matching opt-in role for the user.

Emoji → Role mapping is defined in config.REACTION_ROLES.
"""

import discord
from discord import app_commands
from discord.ext import commands

from config import REACTION_ROLES, ROLE_CEO, ROLE_TEAM_MANAGER


# ── button view ───────────────────────────────────────────────────────────────

class OptInRoleButton(discord.ui.Button):
    def __init__(self, emoji: str, role_name: str):
        super().__init__(
            label=role_name,
            emoji=emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=f"opt_role_{role_name}",
        )
        self.role_name = role_name

    async def callback(self, interaction: discord.Interaction):
        guild  = interaction.guild
        member = interaction.user
        role   = discord.utils.get(guild.roles, name=self.role_name)

        if role is None:
            await interaction.response.send_message(
                f"❌ Role **{self.role_name}** not found on this server.", ephemeral=True
            )
            return

        if role in member.roles:
            await member.remove_roles(role, reason="Opt-in role button")
            await interaction.response.send_message(
                f"✅ Removed **{self.role_name}**.", ephemeral=True
            )
        else:
            await member.add_roles(role, reason="Opt-in role button")
            await interaction.response.send_message(
                f"✅ Added **{self.role_name}**.", ephemeral=True
            )


class OptInRolesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for emoji, role_name in REACTION_ROLES.items():
            self.add_item(OptInRoleButton(emoji, role_name))


# ── cog ───────────────────────────────────────────────────────────────────────

class ReactionRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Register the persistent view so button clicks work after restarts.
        self.bot.add_view(OptInRolesView())

    # ── slash command ─────────────────────────────────────────────────────────

    @app_commands.command(name="post-roles", description="Post the opt-in role selection message here.")
    @app_commands.checks.has_any_role(ROLE_CEO, ROLE_TEAM_MANAGER)
    async def post_roles(self, interaction: discord.Interaction):
        lines = [
            "**🎭 Server Roles**\n",
            "**Team roles** — use `/role-request` to apply:",
            "🏎️ **Driver** — Active race driver",
            "🔧 **Engineer** — Race/setup engineer",
            "🎨 **Livery-Designer** — Livery designer",
            "👤 **Visitor** — Spectator / visitor",
            "🔔 **Updates-Only** — News & updates only",
            "\n**Opt-in roles** — click a button to add or remove:",
        ]

        view = OptInRolesView()
        msg = await interaction.channel.send("\n".join(lines), view=view)

        try:
            await msg.pin()
        except discord.Forbidden:
            pass

        await interaction.response.send_message("✅ Role selection message posted.", ephemeral=True)

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
