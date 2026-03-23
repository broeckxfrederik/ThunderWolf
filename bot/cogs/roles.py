"""
Roles cog
─────────
/role-request role:…
    Any member can request a role change (e.g. Driver → Engineer).
    Posts a request card in the private #role-requests channel.
    CEO / TM approves or denies via buttons on the card.

    On Approve: team role swapped, member notified via DM.
    On Deny:    TM enters a brief reason in a modal, member notified.

All requests are persisted in SQLite (role_requests table).
"""

import discord
from discord import app_commands
from discord.ext import commands

import db
from config import (
    ROLE_DRIVER, ROLE_ENGINEER, ROLE_LIVERY, ROLE_VISITOR, ROLE_UPDATES,
    ROLE_CEO, ROLE_TEAM_MANAGER,
    CFG_ROLE_DRIVER, CFG_ROLE_ENGINEER, CFG_ROLE_LIVERY,
    CFG_ROLE_VISITOR, CFG_ROLE_UPDATES,
    CFG_CH_ROLE_REQ, CHANNEL_ROLE_REQUESTS,
)

# All assignable team roles in priority order
TEAM_ROLES = [
    (CFG_ROLE_DRIVER,   ROLE_DRIVER),
    (CFG_ROLE_ENGINEER, ROLE_ENGINEER),
    (CFG_ROLE_LIVERY,   ROLE_LIVERY),
    (CFG_ROLE_VISITOR,  ROLE_VISITOR),
    (CFG_ROLE_UPDATES,  ROLE_UPDATES),
]

ROLE_CHOICES = [
    app_commands.Choice(name="Driver",          value=ROLE_DRIVER),
    app_commands.Choice(name="Engineer",         value=ROLE_ENGINEER),
    app_commands.Choice(name="Livery Designer",  value=ROLE_LIVERY),
    app_commands.Choice(name="Visitor",          value=ROLE_VISITOR),
    app_commands.Choice(name="Updates Only",     value=ROLE_UPDATES),
]


# ── helpers ───────────────────────────────────────────────────────────────────

def _resolve_role(guild: discord.Guild, cfg_key: str, fallback_name: str) -> discord.Role | None:
    raw_id = db.get_config(guild.id, cfg_key)
    if raw_id:
        role = guild.get_role(int(raw_id))
        if role:
            return role
    return discord.utils.get(guild.roles, name=fallback_name)


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
    ceo_role = _resolve_role(guild, "role_ceo", ROLE_CEO)
    tm_role  = _resolve_role(guild, "role_tm",  ROLE_TEAM_MANAGER)
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

    embed = discord.Embed(
        title="🔄 Role Request",
        colour=colour,
    )
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

    def __init__(self, request_id: int, member: discord.Member, requested_role: str):
        super().__init__()
        self.request_id    = request_id
        self.member        = member
        self.requested_role = requested_role

    async def on_submit(self, interaction: discord.Interaction):
        db.update_request_status(self.request_id, "denied")

        reason_text = self.reason.value.strip() or "No reason given."
        try:
            await self.member.send(
                f"❌ Your request for the **{self.requested_role}** role was **denied**.\n"
                f"Reason: {reason_text}"
            )
        except discord.Forbidden:
            pass

        # Update the card embed
        req = db.get_role_request(self.request_id)
        current_role = _current_team_role(self.member)
        embed = _request_embed(self.member, current_role, self.requested_role, "denied")
        await interaction.response.edit_message(embed=embed, view=None)


# ── request card view ─────────────────────────────────────────────────────────

class RequestCardView(discord.ui.View):
    def __init__(self, request_id: int, member: discord.Member, requested_role: str):
        super().__init__(timeout=None)
        self.request_id     = request_id
        self.member         = member
        self.requested_role = requested_role

    async def _check_authority(self, interaction: discord.Interaction) -> bool:
        guild    = interaction.guild
        ceo_role = _resolve_role(guild, "role_ceo", ROLE_CEO)
        tm_role  = _resolve_role(guild, "role_tm",  ROLE_TEAM_MANAGER)
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

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success,
                       custom_id="rr_approve")
    async def approve(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._check_authority(interaction):
            return

        guild  = interaction.guild
        member = guild.get_member(self.member.id)
        if member is None:
            await interaction.response.send_message(
                "❌ Member not found in the server.", ephemeral=True
            )
            return

        # Remove all current team roles
        for cfg_key, fallback in TEAM_ROLES:
            role = _resolve_role(guild, cfg_key, fallback)
            if role and role in member.roles:
                await member.remove_roles(role, reason="Role request approved")

        # Find and assign the requested role
        new_role = discord.utils.get(guild.roles, name=self.requested_role)
        if new_role is None:
            # Try resolving via config
            for cfg_key, fallback in TEAM_ROLES:
                if fallback == self.requested_role:
                    new_role = _resolve_role(guild, cfg_key, fallback)
                    break

        if new_role:
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
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.danger,
                       custom_id="rr_deny")
    async def deny(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._check_authority(interaction):
            return
        await interaction.response.send_modal(
            DenyModal(self.request_id, self.member, self.requested_role)
        )


# ── cog ───────────────────────────────────────────────────────────────────────

class Roles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="role-request",
        description="Request a role change (e.g. Driver → Engineer).",
    )
    @app_commands.describe(role="The role you want")
    @app_commands.choices(role=ROLE_CHOICES)
    async def role_request(self, interaction: discord.Interaction, role: str):
        member       = interaction.user
        guild        = interaction.guild
        current_role = _current_team_role(member)

        if current_role == role:
            await interaction.response.send_message(
                f"You already have the **{role}** role.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Create DB record
        req_id = db.create_role_request(guild.id, member.id, role)

        # Post card in #role-requests
        ch = await _get_role_requests_channel(guild)
        if ch is None:
            await interaction.followup.send(
                "❌ Could not find or create the #role-requests channel. "
                "Ask a CEO/TM to run `/setup`.",
                ephemeral=True,
            )
            return

        embed = _request_embed(member, current_role, role)
        view  = RequestCardView(req_id, member, role)
        msg   = await ch.send(embed=embed, view=view)

        db.set_request_message(req_id, msg.id, ch.id)
        self.bot.add_view(view)

        await interaction.followup.send(
            f"✅ Your request for **{role}** has been submitted. "
            "A Team Manager or CEO will review it shortly.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Roles(bot))
