"""
Greeting cog
────────────
• Listens for new members joining the guild.
• Creates a temporary private channel  #welcome-<username>  inside a
  "New Members" category that is only visible to the guild owner and CEO.
  The channel itself is also visible to the new member.
• After the member picks a role the channel is automatically deleted.
• If they don't pick:
    - After WELCOME_REMINDER_DAYS (2) days  → reminder message in the channel.
    - After WELCOME_KICK_DAYS (7) days      → member is kicked, channel deleted.
• If Racer is chosen:
    - Creates a permanent private channel  #racer-<username>
      visible to the member, CEO and Team Manager.
    - Posts onboarding instructions that tag Team Manager.
    - A background task checks every hour; if Team Manager has not
      posted in that channel after RACER_REMINDER_DAYS days it tags CEO too.
"""

import asyncio
import dataclasses
import datetime
import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    ROLE_RACER, ROLE_VISITOR, ROLE_UPDATES,
    ROLE_CEO, ROLE_TEAM_MANAGER,
    RACER_REMINDER_DAYS, RACER_ONBOARDING_MSG,
    WELCOME_CATEGORY, WELCOME_REMINDER_DAYS, WELCOME_KICK_DAYS,
)


# ── in-memory state ───────────────────────────────────────────────────────────

@dataclasses.dataclass
class WelcomeEntry:
    member_id:      int
    guild_id:       int
    joined_at:      datetime.datetime
    reminder_sent:  bool = False


# channel_id → WelcomeEntry  (resets on bot restart)
_pending_welcome: dict[int, WelcomeEntry] = {}

# channel_id → created_at  (racer onboarding reminder tracker)
_pending_racer_channels: dict[int, datetime.datetime] = {}


# ── view ──────────────────────────────────────────────────────────────────────

class JoinView(discord.ui.View):
    """Role-picker buttons posted in the temporary welcome channel."""

    def __init__(self, member: discord.Member, channel: discord.TextChannel):
        super().__init__(timeout=None)  # background task handles expiry
        self.member  = member
        self.channel = channel

    # ── helpers ───────────────────────────────────────────────────────────────

    async def _assign(self, role_name: str):
        role = discord.utils.get(self.member.guild.roles, name=role_name)
        if role:
            await self.member.add_roles(role)

    async def _finish(self, interaction: discord.Interaction, label: str):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"✅ Got it! You've been registered as **{label}**.\nThis channel will be removed in a few seconds.",
            view=self,
        )
        self.stop()
        # Unregister so the background task ignores it
        _pending_welcome.pop(self.channel.id, None)
        await asyncio.sleep(5)
        try:
            await self.channel.delete(reason="Welcome flow complete")
        except discord.NotFound:
            pass

    # ── buttons ───────────────────────────────────────────────────────────────

    @discord.ui.button(label="🏎️ Racer", style=discord.ButtonStyle.primary)
    async def btn_racer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._assign(ROLE_RACER)
        await self._finish(interaction, "Racer")
        # Racer channel creation runs after the channel is deleted
        asyncio.create_task(
            interaction.client.cogs["Greeting"]._create_racer_channel(self.member)  # type: ignore[attr-defined]
        )

    @discord.ui.button(label="👀 Just Visiting", style=discord.ButtonStyle.secondary)
    async def btn_visitor(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._assign(ROLE_VISITOR)
        await self._finish(interaction, "Visitor")

    @discord.ui.button(label="📢 Updates Only", style=discord.ButtonStyle.secondary)
    async def btn_updates(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._assign(ROLE_UPDATES)
        await self._finish(interaction, "Updates Only")


# ── cog ───────────────────────────────────────────────────────────────────────

class Greeting(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_welcome_channels.start()
        self.check_racer_channels.start()

    def cog_unload(self):
        self.check_welcome_channels.cancel()
        self.check_racer_channels.cancel()

    # ── welcome category ──────────────────────────────────────────────────────

    async def _get_or_create_welcome_category(self, guild: discord.Guild) -> discord.CategoryChannel:
        """Return the 'New Members' category, creating it if absent.

        Visible to: guild owner, CEO role only.
        Individual welcome channels add their member on top of this.
        """
        category = discord.utils.get(guild.categories, name=WELCOME_CATEGORY)
        if category:
            return category

        ceo_role = discord.utils.get(guild.roles, name=ROLE_CEO)
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True, send_messages=True),
        }
        if guild.owner:
            overwrites[guild.owner] = discord.PermissionOverwrite(view_channel=True)
        if ceo_role:
            overwrites[ceo_role] = discord.PermissionOverwrite(view_channel=True)

        return await guild.create_category(
            name=WELCOME_CATEGORY,
            overwrites=overwrites,
            reason="Welcome channels category",
        )

    # ── new member ────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self._run_welcome(member)

    async def _run_welcome(self, member: discord.Member):
        guild    = member.guild
        category = await self._get_or_create_welcome_category(guild)

        # Channel is visible to owner/CEO (inherited from category) + the member
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member:             discord.PermissionOverwrite(view_channel=True, send_messages=False),
        }
        channel = await guild.create_text_channel(
            name=f"welcome-{member.name}",
            category=category,
            overwrites=overwrites,
            reason="Member welcome channel",
        )

        view = JoinView(member, channel)
        await channel.send(
            f"👋 Hey {member.mention}, welcome to **{guild.name}**!\n\n"
            "What brings you here? Pick your role below:",
            view=view,
        )

        # Register for background follow-up
        _pending_welcome[channel.id] = WelcomeEntry(
            member_id=member.id,
            guild_id=guild.id,
            joined_at=datetime.datetime.utcnow(),
        )

    # ── background: reminder + kick for non-responders ────────────────────────

    @tasks.loop(hours=1)
    async def check_welcome_channels(self):
        now = datetime.datetime.utcnow()

        for channel_id, entry in list(_pending_welcome.items()):
            age = now - entry.joined_at

            channel = self.bot.get_channel(channel_id)
            guild   = self.bot.get_guild(entry.guild_id)
            if guild is None:
                _pending_welcome.pop(channel_id, None)
                continue

            member = guild.get_member(entry.member_id)

            # ── 7 days: kick + delete ─────────────────────────────────────────
            if age >= datetime.timedelta(days=WELCOME_KICK_DAYS):
                if member:
                    try:
                        await member.kick(reason="Did not pick a role within 7 days.")
                    except discord.Forbidden:
                        pass
                if channel:
                    try:
                        await channel.delete(reason="Welcome timed out (7 days).")
                    except discord.NotFound:
                        pass
                _pending_welcome.pop(channel_id, None)
                continue

            # ── 2 days: reminder ──────────────────────────────────────────────
            if (
                age >= datetime.timedelta(days=WELCOME_REMINDER_DAYS)
                and not entry.reminder_sent
                and channel
                and member
            ):
                await channel.send(
                    f"👋 Hey {member.mention}, just a reminder to pick your role above!\n"
                    f"If no choice is made within **{WELCOME_KICK_DAYS} days** of joining "
                    "you will be automatically removed from the server."
                )
                entry.reminder_sent = True

    @check_welcome_channels.before_loop
    async def before_check_welcome(self):
        await self.bot.wait_until_ready()

    # ── racer channel creation ────────────────────────────────────────────────

    async def _create_racer_channel(self, member: discord.Member):
        guild    = member.guild
        ceo_role = discord.utils.get(guild.roles, name=ROLE_CEO)
        tm_role  = discord.utils.get(guild.roles, name=ROLE_TEAM_MANAGER)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member:             discord.PermissionOverwrite(view_channel=True, send_messages=True),
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

        _pending_racer_channels[channel.id] = datetime.datetime.utcnow()

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
        now      = datetime.datetime.utcnow()
        deadline = datetime.timedelta(days=RACER_REMINDER_DAYS)
        to_remove = []

        for channel_id, created_at in list(_pending_racer_channels.items()):
            if now - created_at < deadline:
                continue

            channel = self.bot.get_channel(channel_id)
            if channel is None:
                to_remove.append(channel_id)
                continue

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
                if ceo_role:
                    await channel.send(
                        f"⚠️ {ceo_role.mention} — no response from {ROLE_TEAM_MANAGER} "
                        f"after {RACER_REMINDER_DAYS} days. Please follow up!"
                    )
                to_remove.append(channel_id)

        for cid in to_remove:
            _pending_racer_channels.pop(cid, None)

    @check_racer_channels.before_loop
    async def before_check_racer(self):
        await self.bot.wait_until_ready()

    # ── test command ──────────────────────────────────────────────────────────

    @app_commands.command(
        name="test-welcome",
        description="Trigger the welcome channel flow for a user (CEO / Team Manager only).",
    )
    @app_commands.describe(user="The member to send the welcome message to")
    @app_commands.checks.has_any_role(ROLE_CEO, ROLE_TEAM_MANAGER)
    async def test_welcome(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)
        try:
            await self._run_welcome(user)
            await interaction.followup.send(
                f"✅ Welcome channel created for {user.mention}.", ephemeral=True
            )
        except discord.Forbidden as e:
            await interaction.followup.send(
                f"❌ Missing permission: `{e.text}`\n"
                "Make sure the bot has **Manage Channels** in the server.", ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Unexpected error: `{type(e).__name__}: {e}`", ephemeral=True
            )

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
