"""
Greeting cog
────────────
• Listens for new members joining the guild.
• Creates a temporary private channel  #welcome-<username>  inside the
  configured welcome category (visible to guild owner, CEO, TM, and the member).
• Member picks their role via buttons:
    Driver | Engineer | Livery Designer | Visitor | Updates Only
• On pick: role assigned instantly, channel deleted after a short pause.
• If no pick within 12 hours: member is kicked, channel deleted.
  State is persisted in SQLite so the timeout survives bot restarts.

/test-welcome  — manually trigger the flow for a user (CEO / TM only).
"""

import asyncio
import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
from utils import resolve_role as _resolve_role
from cogs.roles import (
    _get_role_approvals_channel,
    _request_embed,
    _current_team_role,
    RequestCardView,
)
from config import (
    ROLE_DRIVER, ROLE_ENGINEER, ROLE_LIVERY, ROLE_VISITOR, ROLE_UPDATES,
    ROLE_CEO, ROLE_TEAM_MANAGER,
    CFG_ROLE_DRIVER, CFG_ROLE_ENGINEER, CFG_ROLE_LIVERY,
    CFG_ROLE_VISITOR, CFG_ROLE_UPDATES, CFG_ROLE_CEO, CFG_ROLE_TM,
    CFG_CAT_WELCOME, WELCOME_CATEGORY,
    WELCOME_TIMEOUT_HOURS,
)


# ── helpers ───────────────────────────────────────────────────────────────────

async def _get_or_create_welcome_category(guild: discord.Guild) -> discord.CategoryChannel:
    raw_id = db.get_config(guild.id, CFG_CAT_WELCOME)
    if raw_id:
        cat = guild.get_channel(int(raw_id))
        if isinstance(cat, discord.CategoryChannel):
            return cat

    cat = discord.utils.get(guild.categories, name=WELCOME_CATEGORY)
    if cat:
        return cat

    ceo_role = _resolve_role(guild, CFG_ROLE_CEO, ROLE_CEO)
    tm_role  = _resolve_role(guild, CFG_ROLE_TM,  ROLE_TEAM_MANAGER)
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me:           discord.PermissionOverwrite(
            view_channel=True, manage_channels=True, send_messages=True
        ),
    }
    if guild.owner:
        overwrites[guild.owner] = discord.PermissionOverwrite(view_channel=True)
    if ceo_role:
        overwrites[ceo_role] = discord.PermissionOverwrite(view_channel=True)
    if tm_role:
        overwrites[tm_role] = discord.PermissionOverwrite(view_channel=True)

    cat = await guild.create_category(
        name=WELCOME_CATEGORY,
        overwrites=overwrites,
        reason="Welcome channels category",
    )
    db.set_config(guild.id, CFG_CAT_WELCOME, str(cat.id))
    return cat


# ── join view ─────────────────────────────────────────────────────────────────

class JoinView(discord.ui.View):
    """Role-picker shown in the temporary welcome channel."""

    def __init__(self, member: discord.Member, channel: discord.TextChannel):
        super().__init__(timeout=None)
        self.member  = member
        self.channel = channel

    async def _pick(
        self,
        interaction: discord.Interaction,
        cfg_key: str,
        fallback_name: str,
        label: str,
    ):
        if interaction.user.id != self.member.id:
            await interaction.response.send_message(
                "This welcome is not for you.", ephemeral=True
            )
            return

        # Respond immediately — role assignment must not block the Discord response
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"✅ Got it! You've been registered as **{label}**. Welcome!",
            view=self,
        )
        self.stop()

        role = _resolve_role(self.member.guild, cfg_key, fallback_name)
        if role:
            try:
                await self.member.add_roles(role, reason=f"Welcome role pick: {label}")
            except discord.Forbidden:
                await interaction.followup.send(
                    f"⚠️ Could not assign the **{label}** role — missing permissions. "
                    "Please ask a TM/CEO to assign it manually.",
                    ephemeral=True,
                )

        db.remove_welcome(self.channel.id)
        await asyncio.sleep(5)
        try:
            await self.channel.delete(reason="Welcome flow complete")
        except discord.NotFound:
            pass

    async def _request_pick(
        self,
        interaction: discord.Interaction,
        fallback_name: str,
        label: str,
    ):
        """Respond immediately then create a role-request card for TM/CEO approval."""
        if interaction.user.id != self.member.id:
            await interaction.response.send_message(
                "This welcome is not for you.", ephemeral=True
            )
            return

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=(
                f"📋 Your request for **{label}** has been submitted! "
                "A team manager will review it shortly."
            ),
            view=self,
        )
        self.stop()

        guild  = self.member.guild
        req_id = db.create_role_request(guild.id, self.member.id, fallback_name)

        approvals_ch = await _get_role_approvals_channel(guild)
        if approvals_ch:
            current = _current_team_role(self.member)
            embed   = _request_embed(self.member, current, fallback_name, "pending")
            view    = RequestCardView(req_id, self.member.id, fallback_name)
            msg     = await approvals_ch.send(embed=embed, view=view)
            interaction.client.add_view(view)
            db.set_request_message(req_id, msg.id, approvals_ch.id)

        db.remove_welcome(self.channel.id)
        await asyncio.sleep(5)
        try:
            await self.channel.delete(reason="Welcome flow complete")
        except discord.NotFound:
            pass

    @discord.ui.button(label="🏎️ Driver",          style=discord.ButtonStyle.primary)
    async def btn_driver(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._pick(interaction, CFG_ROLE_DRIVER, ROLE_DRIVER, "Driver")

    @discord.ui.button(label="🔧 Engineer",         style=discord.ButtonStyle.primary)
    async def btn_engineer(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._request_pick(interaction, ROLE_ENGINEER, "Engineer")

    @discord.ui.button(label="🎨 Livery Designer",  style=discord.ButtonStyle.primary)
    async def btn_livery(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._request_pick(interaction, ROLE_LIVERY, "Livery Designer")

    @discord.ui.button(label="👀 Just Visiting",    style=discord.ButtonStyle.secondary, row=1)
    async def btn_visitor(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._pick(interaction, CFG_ROLE_VISITOR, ROLE_VISITOR, "Visitor")

    @discord.ui.button(label="📢 Updates Only",     style=discord.ButtonStyle.secondary, row=1)
    async def btn_updates(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._pick(interaction, CFG_ROLE_UPDATES, ROLE_UPDATES, "Updates Only")


# ── cog ───────────────────────────────────────────────────────────────────────

class Greeting(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_welcome_channels.start()

    def cog_unload(self):
        self.check_welcome_channels.cancel()

    # ── new member ────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self._run_welcome(member)

    async def _run_welcome(self, member: discord.Member):
        guild    = member.guild
        category = await _get_or_create_welcome_category(guild)

        ceo_role = _resolve_role(guild, CFG_ROLE_CEO, ROLE_CEO)
        tm_role  = _resolve_role(guild, CFG_ROLE_TM,  ROLE_TEAM_MANAGER)

        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me:           discord.PermissionOverwrite(
                view_channel=True, manage_channels=True, send_messages=True
            ),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        }
        if guild.owner:
            overwrites[guild.owner] = discord.PermissionOverwrite(view_channel=True)
        if ceo_role:
            overwrites[ceo_role] = discord.PermissionOverwrite(view_channel=True)
        if tm_role:
            overwrites[tm_role] = discord.PermissionOverwrite(view_channel=True)

        channel = await guild.create_text_channel(
            name=f"welcome-{member.name}",
            category=category,
            overwrites=overwrites,
            reason="Member welcome channel",
        )

        view = JoinView(member, channel)
        await channel.send(
            f"👋 Hey {member.mention}, welcome to **{guild.name}**!\n\n"
            "Pick the role that best describes you below.\n"
            "This channel will be removed automatically after you pick "
            f"(or in {WELCOME_TIMEOUT_HOURS}h if you don't).",
            view=view,
        )

        db.add_welcome(
            channel_id=channel.id,
            guild_id=guild.id,
            member_id=member.id,
            created_at=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat(),
        )

    # ── background: 12h kick for non-responders ───────────────────────────────

    @tasks.loop(minutes=30)
    async def check_welcome_channels(self):
        cutoff = (
            datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(hours=WELCOME_TIMEOUT_HOURS)
        ).isoformat()
        expired = db.get_expired_welcomes(cutoff)

        for row in expired:
            guild   = self.bot.get_guild(row["guild_id"])
            if guild is None:
                db.remove_welcome(row["channel_id"])
                continue

            member  = guild.get_member(row["member_id"])
            channel = self.bot.get_channel(row["channel_id"])

            if member:
                try:
                    await member.kick(
                        reason=f"Did not pick a role within {WELCOME_TIMEOUT_HOURS}h."
                    )
                except discord.Forbidden:
                    pass

            if channel:
                try:
                    await channel.delete(reason="Welcome timed out.")
                except discord.NotFound:
                    pass

            db.remove_welcome(row["channel_id"])

    @check_welcome_channels.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    # ── /test-welcome ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="test-welcome",
        description="Trigger the welcome channel flow for a user (CEO / TM only).",
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
                "Make sure the bot has **Manage Channels** in the server.",
                ephemeral=True,
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
