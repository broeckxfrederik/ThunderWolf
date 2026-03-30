"""
Roles cog
─────────
/role-request role:…
    Any member can request a role change (e.g. Driver → Engineer).
    Team roles (Driver, Engineer, etc.) post a request card in the private
    #role-requests channel for CEO/TM to approve or deny.
    Opt-in notification roles are assigned immediately without approval.

    On Approve: team role swapped, member notified via DM.
    On Deny:    TM enters a brief reason in a modal, member notified.

All requests are persisted in SQLite (role_requests table).
"""

import discord
from discord import app_commands
from discord.ext import commands

import db
from utils import resolve_role as _resolve_role
from config import (
    ROLE_DRIVER, ROLE_ENGINEER, ROLE_LIVERY, ROLE_VISITOR, ROLE_UPDATES,
    ROLE_F1, ROLE_TWITCH, ROLE_DRIVER_NOTIF,
    ROLE_CEO, ROLE_TEAM_MANAGER,
    CFG_ROLE_DRIVER, CFG_ROLE_ENGINEER, CFG_ROLE_LIVERY,
    CFG_ROLE_VISITOR, CFG_ROLE_UPDATES, CFG_ROLE_CEO, CFG_ROLE_TM,
    CFG_CH_ROLE_REQ, CHANNEL_ROLE_REQUESTS,
    CFG_CH_ROLE_APPROVALS, CHANNEL_ROLE_APPROVALS,
)

# All assignable team roles in priority order
TEAM_ROLES = [
    (CFG_ROLE_DRIVER,   ROLE_DRIVER),
    (CFG_ROLE_ENGINEER, ROLE_ENGINEER),
    (CFG_ROLE_LIVERY,   ROLE_LIVERY),
    (CFG_ROLE_VISITOR,  ROLE_VISITOR),
    (CFG_ROLE_UPDATES,  ROLE_UPDATES),
]

# Opt-in roles that don't need TM approval
OPT_IN_ROLES = [ROLE_F1, ROLE_TWITCH, ROLE_DRIVER_NOTIF]

ROLE_CHOICES = [
    app_commands.Choice(name="Driver",                value=ROLE_DRIVER),
    app_commands.Choice(name="Engineer",              value=ROLE_ENGINEER),
    app_commands.Choice(name="Livery Designer",       value=ROLE_LIVERY),
    app_commands.Choice(name="Visitor",               value=ROLE_VISITOR),
    app_commands.Choice(name="Updates Only",          value=ROLE_UPDATES),
    app_commands.Choice(name="F1 Updates",            value=ROLE_F1),
    app_commands.Choice(name="Twitch Notifications",  value=ROLE_TWITCH),
    app_commands.Choice(name="Driver Notification",   value=ROLE_DRIVER_NOTIF),
]


# ── helpers ───────────────────────────────────────────────────────────────────

def _current_team_role(member: discord.Member) -> str:
    """Return the member's current team role name, or 'None'."""
    for cfg_key, fallback in TEAM_ROLES:
        role = _resolve_role(member.guild, cfg_key, fallback)
        if role and role in member.roles:
            return role.name
    return "None"


async def _get_role_requests_channel(guild: discord.Guild) -> discord.TextChannel | None:
    raw_id = db.get_config(guild.id, CFG_CH_ROLE_REQ)
    if raw_id:
        ch = guild.get_channel(int(raw_id))
        if isinstance(ch, discord.TextChannel):
            return ch

    ch = discord.utils.get(guild.text_channels, name=CHANNEL_ROLE_REQUESTS)
    if ch:
        db.set_config(guild.id, CFG_CH_ROLE_REQ, str(ch.id))
        return ch

    # Create it — visible to CEO and TM only
    ceo_role = _resolve_role(guild, CFG_ROLE_CEO, ROLE_CEO)
    tm_role  = _resolve_role(guild, CFG_ROLE_TM,  ROLE_TEAM_MANAGER)
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me:           discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    if ceo_role:
        overwrites[ceo_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    if tm_role:
        overwrites[tm_role]  = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    try:
        ch = await guild.create_text_channel(
            CHANNEL_ROLE_REQUESTS,
            overwrites=overwrites,
            reason="Role requests channel",
        )
        db.set_config(guild.id, CFG_CH_ROLE_REQ, str(ch.id))
        return ch
    except discord.Forbidden:
        return None


async def _get_role_approvals_channel(guild: discord.Guild) -> discord.TextChannel | None:
    """Private TM/CEO-only channel where approval cards with buttons are posted."""
    raw_id = db.get_config(guild.id, CFG_CH_ROLE_APPROVALS)
    if raw_id:
        ch = guild.get_channel(int(raw_id))
        if isinstance(ch, discord.TextChannel):
            return ch

    ch = discord.utils.get(guild.text_channels, name=CHANNEL_ROLE_APPROVALS)
    if ch:
        db.set_config(guild.id, CFG_CH_ROLE_APPROVALS, str(ch.id))
        return ch

    ceo_role = _resolve_role(guild, CFG_ROLE_CEO, ROLE_CEO)
    tm_role  = _resolve_role(guild, CFG_ROLE_TM,  ROLE_TEAM_MANAGER)
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me:           discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    if ceo_role:
        overwrites[ceo_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    if tm_role:
        overwrites[tm_role]  = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    try:
        ch = await guild.create_text_channel(
            CHANNEL_ROLE_APPROVALS,
            overwrites=overwrites,
            reason="Role approvals channel",
        )
        db.set_config(guild.id, CFG_CH_ROLE_APPROVALS, str(ch.id))
        return ch
    except discord.Forbidden:
        return None


def _request_embed(
    member: discord.Member,
    current_role: str,
    requested_role: str,
    status: str = "pending",
) -> discord.Embed:
    colour = {
        "pending":  discord.Colour.orange(),
        "approved": discord.Colour.green(),
        "denied":   discord.Colour.red(),
    }.get(status, discord.Colour.orange())

    embed = discord.Embed(title="🔄 Role Request", colour=colour)
    embed.add_field(name="Member",         value=member.mention,   inline=True)
    embed.add_field(name="Current Role",   value=current_role,     inline=True)
    embed.add_field(name="Requested Role", value=requested_role,   inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Status: {status.capitalize()}")
    return embed


# ── deny modal ────────────────────────────────────────────────────────────────

class DenyModal(discord.ui.Modal, title="Deny Role Request"):
    reason = discord.ui.TextInput(
        label="Reason (sent to the member)",
        style=discord.TextStyle.paragraph,
        required=False,
        placeholder="Optional — leave blank for a generic denial.",
    )

    def __init__(self, request_id: int, member_id: int, requested_role: str):
        super().__init__()
        self.request_id     = request_id
        self.member_id      = member_id
        self.requested_role = requested_role

    async def on_submit(self, interaction: discord.Interaction):
        db.update_request_status(self.request_id, "denied")

        guild  = interaction.guild
        member = guild.get_member(self.member_id)
        if member is None:
            try:
                member = await guild.fetch_member(self.member_id)
            except discord.NotFound:
                member = None

        reason_text = self.reason.value.strip() or "No reason given."
        if member:
            try:
                await member.send(
                    f"❌ Your request for the **{self.requested_role}** role was **denied**.\n"
                    f"Reason: {reason_text}"
                )
            except discord.Forbidden:
                pass
            current_role = _current_team_role(member)
            embed = _request_embed(member, current_role, self.requested_role, "denied")
        else:
            embed = discord.Embed(
                title="🔄 Role Request",
                colour=discord.Colour.red(),
                description=f"Request for **{self.requested_role}** denied (member left the server).",
            )
            embed.set_footer(text="Status: Denied")

        await interaction.response.edit_message(embed=embed, view=None)


# ── request card view ─────────────────────────────────────────────────────────

class RequestCardView(discord.ui.View):
    def __init__(self, request_id: int, member_id: int, requested_role: str):
        super().__init__(timeout=None)
        self.request_id     = request_id
        self.member_id      = member_id
        self.requested_role = requested_role

        approve_btn = discord.ui.Button(
            label="✅ Approve",
            style=discord.ButtonStyle.success,
            custom_id=f"rr_approve_{request_id}",
        )
        approve_btn.callback = self._approve
        self.add_item(approve_btn)

        deny_btn = discord.ui.Button(
            label="❌ Deny",
            style=discord.ButtonStyle.danger,
            custom_id=f"rr_deny_{request_id}",
        )
        deny_btn.callback = self._deny
        self.add_item(deny_btn)

    async def _get_member(self, guild: discord.Guild) -> discord.Member | None:
        member = guild.get_member(self.member_id)
        if member is None:
            try:
                member = await guild.fetch_member(self.member_id)
            except discord.NotFound:
                pass
        return member

    async def _check_authority(self, interaction: discord.Interaction) -> bool:
        guild    = interaction.guild
        ceo_role = _resolve_role(guild, CFG_ROLE_CEO, ROLE_CEO)
        tm_role  = _resolve_role(guild, CFG_ROLE_TM,  ROLE_TEAM_MANAGER)
        user     = interaction.user
        has_auth = (
            (ceo_role and ceo_role in user.roles) or
            (tm_role  and tm_role  in user.roles)
        )
        if not has_auth:
            await interaction.response.send_message(
                "❌ Only CEO or Team Manager can approve/deny role requests.", ephemeral=True
            )
        return has_auth

    async def _approve(self, interaction: discord.Interaction):
        # Auth check is synchronous — respond immediately if unauthorized
        guild    = interaction.guild
        ceo_role = _resolve_role(guild, CFG_ROLE_CEO, ROLE_CEO)
        tm_role  = _resolve_role(guild, CFG_ROLE_TM,  ROLE_TEAM_MANAGER)
        has_auth = (
            (ceo_role and ceo_role in interaction.user.roles) or
            (tm_role  and tm_role  in interaction.user.roles)
        )
        if not has_auth:
            await interaction.response.send_message(
                "❌ Only CEO or Team Manager can approve/deny role requests.", ephemeral=True
            )
            return

        # Defer now — the next steps involve multiple API calls which can
        # easily exceed Discord's 3-second interaction response deadline.
        await interaction.response.defer()

        member = await self._get_member(guild)
        if member is None:
            await interaction.followup.send("❌ Member not found in the server.", ephemeral=True)
            return

        # Defer now — the next steps involve multiple API calls which can
        # easily exceed Discord's 3-second interaction response deadline.
        await interaction.response.defer()

        try:
            member = await self._get_member(guild)
            if member is None:
                await interaction.followup.send("❌ Member not found in the server.", ephemeral=True)
                return

            # Find the requested role first — fail early if it doesn't exist
            new_role = discord.utils.get(guild.roles, name=self.requested_role)
            if new_role is None:
                for cfg_key, fallback in TEAM_ROLES:
                    if fallback == self.requested_role:
                        new_role = _resolve_role(guild, cfg_key, fallback)
                        break

            if new_role is None:
                await interaction.followup.send(
                    f"❌ Role **{self.requested_role}** not found on this server. "
                    "Ask a CEO/TM to run `/setup` to link it first.",
                    ephemeral=True,
                )
                return

            # Remove all current team roles
            for cfg_key, fallback in TEAM_ROLES:
                role = _resolve_role(guild, cfg_key, fallback)
                if role and role in member.roles:
                    await member.remove_roles(role, reason="Role request approved")

            await member.add_roles(new_role, reason="Role request approved")
            db.update_request_status(self.request_id, "approved")

            try:
                await member.send(
                    f"✅ Your request for the **{self.requested_role}** role was **approved**! "
                    "Your role has been updated."
                )
            except discord.Forbidden:
                pass

            current_role = _current_team_role(member)
            embed = _request_embed(member, current_role, self.requested_role, "approved")
            await interaction.message.edit(embed=embed, view=None)

        current_role = _current_team_role(member)
        embed = _request_embed(member, current_role, self.requested_role, "approved")
        await interaction.message.edit(embed=embed, view=None)

    async def _deny(self, interaction: discord.Interaction):
        if not await self._check_authority(interaction):
            return
        await interaction.response.send_modal(
            DenyModal(self.request_id, self.member_id, self.requested_role)
        )


# ── cog ───────────────────────────────────────────────────────────────────────

class Roles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Restore RequestCardViews for all pending requests after restart.
        self.bot.loop.create_task(self._restore_views())

    async def _restore_views(self):
        await self.bot.wait_until_ready()
        for row in db.get_pending_role_requests():
            # No member lookup needed — view resolves member lazily on interaction.
            view = RequestCardView(row["id"], row["member_id"], row["requested_role"])
            self.bot.add_view(view)

    @app_commands.command(
        name="role-request",
        description="Request a role (team roles require TM approval; opt-in roles are instant).",
    )
    @app_commands.describe(role="The role you want")
    @app_commands.choices(role=ROLE_CHOICES)
    async def role_request(self, interaction: discord.Interaction, role: str):
        member = interaction.user
        guild  = interaction.guild

        # Opt-in roles: grant/remove immediately without TM approval
        if role in OPT_IN_ROLES:
            discord_role = discord.utils.get(guild.roles, name=role)
            if discord_role is None:
                await interaction.response.send_message(
                    f"❌ Role **{role}** not found on this server.", ephemeral=True
                )
                return
            if discord_role in member.roles:
                await member.remove_roles(discord_role, reason="Role request self-serve")
                await interaction.response.send_message(
                    f"✅ Removed **{role}**.", ephemeral=True
                )
            else:
                await member.add_roles(discord_role, reason="Role request self-serve")
                await interaction.response.send_message(
                    f"✅ Added **{role}**.", ephemeral=True
                )
            return

        # Team roles: go through approval flow
        current_role = _current_team_role(member)
        if current_role == role:
            await interaction.response.send_message(
                f"You already have the **{role}** role.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        req_id = db.create_role_request(guild.id, member.id, role)

        # Post notification in #role-requests (visible log)
        req_ch = await _get_role_requests_channel(guild)

        # Post approval card with buttons in the separate #role-approvals channel
        approval_ch = await _get_role_approvals_channel(guild)
        if approval_ch is None:
            await interaction.followup.send(
                "❌ Could not find or create the #role-approvals channel. "
                "Ask a CEO/TM to run `/setup`.",
                ephemeral=True,
            )
            return

        embed = _request_embed(member, current_role, role)
        view  = RequestCardView(req_id, member.id, role)
        msg   = await approval_ch.send(embed=embed, view=view)

        db.set_request_message(req_id, msg.id, approval_ch.id)
        self.bot.add_view(view)

        # Also log in #role-requests without buttons (no action needed there)
        if req_ch:
            log_embed = _request_embed(member, current_role, role)
            log_embed.set_footer(text=f"Status: Pending | Review in #{approval_ch.name}")
            await req_ch.send(embed=log_embed)

        await interaction.followup.send(
            f"✅ Your request for **{role}** has been submitted. "
            "A Team Manager or CEO will review it shortly.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Roles(bot))
