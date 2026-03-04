"""
Greeting cog
────────────
• Listens for new members joining the guild.
• DMs them with three buttons: Racer / Just Visiting / Updates Only.
• Assigns the chosen role on the guild.
• If Racer is chosen:
    - Creates a private channel  #racer-<username>
      visible only to the member, CEO and Team Manager.
    - Posts onboarding instructions that tag Team Manager.
    - A background task checks every hour; if Team Manager has not
      posted in that channel after RACER_REMINDER_DAYS days it tags CEO too.
"""

import asyncio
import datetime
import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    ROLE_RACER, ROLE_VISITOR, ROLE_UPDATES,
    ROLE_CEO, ROLE_TEAM_MANAGER,
    RACER_REMINDER_DAYS, RACER_ONBOARDING_MSG,
)


# channel_id → datetime when it was created (in-memory; resets on restart)
_pending_racer_channels: dict[int, datetime.datetime] = {}


class JoinView(discord.ui.View):
    """Buttons sent to a new member in their DM."""

    def __init__(self, member: discord.Member):
        super().__init__(timeout=600)  # 10 minutes to respond
        self.member = member

    # ── helpers ───────────────────────────────────────────────────────────────

    async def _assign(self, interaction: discord.Interaction, role_name: str):
        role = discord.utils.get(self.member.guild.roles, name=role_name)
        if role:
            await self.member.add_roles(role)

    async def _disable(self, interaction: discord.Interaction, label: str):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"✅ You've been registered as **{label}**. Welcome!",
            view=self,
        )
        self.stop()

    # ── buttons ───────────────────────────────────────────────────────────────

    @discord.ui.button(label="🏎️ Racer", style=discord.ButtonStyle.primary)
    async def btn_racer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._assign(interaction, ROLE_RACER)
        await self._disable(interaction, "Racer")
        # Hand off to the cog so we can access the bot
        self.chosen = "racer"

    @discord.ui.button(label="👀 Just Visiting", style=discord.ButtonStyle.secondary)
    async def btn_visitor(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._assign(interaction, ROLE_VISITOR)
        await self._disable(interaction, "Visitor")
        self.chosen = "visitor"

    @discord.ui.button(label="📢 Updates Only", style=discord.ButtonStyle.secondary)
    async def btn_updates(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._assign(interaction, ROLE_UPDATES)
        await self._disable(interaction, "Updates Only")
        self.chosen = "updates"


class Greeting(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_racer_channels.start()

    def cog_unload(self):
        self.check_racer_channels.cancel()

    # ── new member ────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        view = JoinView(member)

        try:
            msg = await member.send(
                f"👋 Welcome to **{member.guild.name}**, {member.mention}!\n\n"
                "What brings you here? Pick your role below:",
                view=view,
            )
        except discord.Forbidden:
            return  # user has DMs disabled – nothing we can do

        # Wait for the view to finish so we know what was chosen
        await view.wait()

        if getattr(view, "chosen", None) == "racer":
            await self._create_racer_channel(member)

    # ── racer channel creation ────────────────────────────────────────────────

    async def _create_racer_channel(self, member: discord.Member):
        guild = member.guild

        ceo_role    = discord.utils.get(guild.roles, name=ROLE_CEO)
        tm_role     = discord.utils.get(guild.roles, name=ROLE_TEAM_MANAGER)
        everyone    = guild.default_role

        # Build permission overwrites
        overwrites = {
            everyone: discord.PermissionOverwrite(view_channel=False),
            member:   discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        if ceo_role:
            overwrites[ceo_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        if tm_role:
            overwrites[tm_role]  = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        channel = await guild.create_text_channel(
            name=f"racer-{member.name}",
            overwrites=overwrites,
            topic=f"Racer onboarding for {member} | created:{datetime.datetime.utcnow().isoformat()}",
            reason="Racer onboarding",
        )

        # Record for reminder check
        _pending_racer_channels[channel.id] = datetime.datetime.utcnow()

        # Post onboarding message
        tm_mention = tm_role.mention if tm_role else "@Team-Manager"
        await channel.send(
            RACER_ONBOARDING_MSG.format(
                mention=member.mention,
                team_manager_mention=tm_mention,
            )
        )

    # ── background: remind CEO if TM hasn't responded ────────────────────────

    @tasks.loop(hours=1)
    async def check_racer_channels(self):
        now = datetime.datetime.utcnow()
        deadline = datetime.timedelta(days=RACER_REMINDER_DAYS)
        to_remove = []

        for channel_id, created_at in list(_pending_racer_channels.items()):
            if now - created_at < deadline:
                continue  # not overdue yet

            channel = self.bot.get_channel(channel_id)
            if channel is None:
                to_remove.append(channel_id)
                continue

            # Check if Team Manager (or CEO) has already posted
            tm_role  = discord.utils.get(channel.guild.roles, name=ROLE_TEAM_MANAGER)
            ceo_role = discord.utils.get(channel.guild.roles, name=ROLE_CEO)

            async for msg in channel.history(limit=50):
                if msg.author.bot:
                    continue
                member_roles = [r.id for r in getattr(msg.author, "roles", [])]
                if tm_role and tm_role.id in member_roles:
                    to_remove.append(channel_id)
                    break
            else:
                # No TM response – ping CEO
                if ceo_role:
                    await channel.send(
                        f"⚠️ {ceo_role.mention} — no response from {ROLE_TEAM_MANAGER} "
                        f"after {RACER_REMINDER_DAYS} days. Please follow up!"
                    )
                to_remove.append(channel_id)

        for cid in to_remove:
            _pending_racer_channels.pop(cid, None)

    @check_racer_channels.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    # ── test command ──────────────────────────────────────────────────────────

    @app_commands.command(
        name="test-welcome",
        description="Send the welcome DM to a user as a test (CEO / Team Manager only).",
    )
    @app_commands.describe(user="The member to send the welcome message to")
    @app_commands.checks.has_any_role(ROLE_CEO, ROLE_TEAM_MANAGER)
    async def test_welcome(self, interaction: discord.Interaction, user: discord.Member):
        view = JoinView(user)

        try:
            await user.send(
                f"👋 Welcome to **{interaction.guild.name}**, {user.mention}!\n\n"
                "What brings you here? Pick your role below:",
                view=view,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                f"❌ Could not DM {user.mention} — they have DMs disabled.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"✅ Welcome message sent to {user.mention}.", ephemeral=True
        )

        await view.wait()

        if getattr(view, "chosen", None) == "racer":
            await self._create_racer_channel(user)

    @test_welcome.error
    async def test_welcome_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingAnyRole):
            await interaction.response.send_message(
                "❌ Only CEO or Team Manager can use this command.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Greeting(bot))
