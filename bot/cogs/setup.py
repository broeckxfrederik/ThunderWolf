"""
Setup cog
─────────
/setup        — step-through wizard (CEO only) to link existing roles and
                channels to the bot config, or create them if absent.
/setup-status — show current linked items with ✅ / ❌ per item.

All selections are persisted to the SQLite guild_config table so they
survive bot restarts and redeployments.
"""

import discord
from discord import app_commands
from discord.ext import commands

import db
from utils import resolve_role as _resolve_role
from config import (
    ROLE_DRIVER, ROLE_ENGINEER, ROLE_LIVERY, ROLE_VISITOR, ROLE_UPDATES,
    ROLE_CEO, ROLE_TEAM_MANAGER,
    WELCOME_CATEGORY, RACES_CATEGORY,
    CHANNEL_ROLE_REQUESTS, CHANNEL_ROLE_APPROVALS, CHANNEL_LINEUP,
    CFG_ROLE_DRIVER, CFG_ROLE_ENGINEER, CFG_ROLE_LIVERY,
    CFG_ROLE_VISITOR, CFG_ROLE_UPDATES, CFG_ROLE_CEO, CFG_ROLE_TM,
    CFG_CAT_WELCOME, CFG_CAT_RACES,
    CFG_CH_ROLE_REQ, CFG_CH_ROLE_APPROVALS, CFG_CH_LINEUP,
)

# All 5 welcome roles (cfg_key, fallback_name)
_WELCOME_ROLES = [
    (CFG_ROLE_DRIVER,   ROLE_DRIVER),
    (CFG_ROLE_ENGINEER, ROLE_ENGINEER),
    (CFG_ROLE_LIVERY,   ROLE_LIVERY),
    (CFG_ROLE_VISITOR,  ROLE_VISITOR),
    (CFG_ROLE_UPDATES,  ROLE_UPDATES),
]


# ── wizard step definitions ────────────────────────────────────────────────────

# (config_key, label, kind, default_name)
# kind: "role" | "category" | "text_channel" | "forum"
STEPS: list[tuple[str, str, str, str]] = [
    (CFG_ROLE_DRIVER,    "Driver role",                "role",         ROLE_DRIVER),
    (CFG_ROLE_ENGINEER,  "Engineer role",               "role",         ROLE_ENGINEER),
    (CFG_ROLE_LIVERY,    "Livery Designer role",        "role",         ROLE_LIVERY),
    (CFG_ROLE_VISITOR,   "Visitor role",                "role",         ROLE_VISITOR),
    (CFG_ROLE_UPDATES,   "Updates-Only role",           "role",         ROLE_UPDATES),
    (CFG_ROLE_CEO,       "CEO role",                    "role",         ROLE_CEO),
    (CFG_ROLE_TM,        "Team Manager role",           "role",         ROLE_TEAM_MANAGER),
    (CFG_CAT_WELCOME,    "Welcome category",            "category",     WELCOME_CATEGORY),
    (CFG_CAT_RACES,      "Races category",              "category",     RACES_CATEGORY),
    (CFG_CH_ROLE_REQ,      "Role-requests channel",        "text_channel", CHANNEL_ROLE_REQUESTS),
    (CFG_CH_ROLE_APPROVALS,"Role-approvals channel",       "text_channel", CHANNEL_ROLE_APPROVALS),
    (CFG_CH_LINEUP,        "Team-manager lineups channel", "text_channel", CHANNEL_LINEUP),
]


# ── wizard view ───────────────────────────────────────────────────────────────

class SetupView(discord.ui.View):
    def __init__(self, guild: discord.Guild, step: int = 0):
        super().__init__(timeout=300)
        self.guild = guild
        self.step  = step
        self._build()

    def _build(self):
        self.clear_items()
        key, label, kind, default_name = STEPS[self.step]
        total = len(STEPS)

        if kind == "role":
            sel = discord.ui.RoleSelect(
                placeholder=f"({self.step+1}/{total}) Pick: {label}",
                min_values=1, max_values=1,
            )
        elif kind == "category":
            sel = discord.ui.ChannelSelect(
                placeholder=f"({self.step+1}/{total}) Pick: {label}",
                min_values=1, max_values=1,
                channel_types=[discord.ChannelType.category],
            )
        elif kind == "forum":
            sel = discord.ui.ChannelSelect(
                placeholder=f"({self.step+1}/{total}) Pick: {label}",
                min_values=1, max_values=1,
                channel_types=[discord.ChannelType.forum],
            )
        else:  # text_channel
            sel = discord.ui.ChannelSelect(
                placeholder=f"({self.step+1}/{total}) Pick: {label}",
                min_values=1, max_values=1,
                channel_types=[discord.ChannelType.text],
            )

        # Capture references for the closures
        view      = self
        cfg_key   = key
        def_name  = default_name
        step_kind = kind

        async def on_select(interaction: discord.Interaction):
            selected_id = str(sel.values[0].id)
            db.set_config(view.guild.id, cfg_key, selected_id)
            await view._advance(interaction)

        async def on_create(interaction: discord.Interaction):
            created_id = await _create_resource(view.guild, step_kind, def_name)
            if created_id:
                db.set_config(view.guild.id, cfg_key, str(created_id))
            await view._advance(interaction)

        async def on_skip(interaction: discord.Interaction):
            await view._advance(interaction)

        sel.callback = on_select
        self.add_item(sel)

        create_btn = discord.ui.Button(
            label=f'Create "{default_name}"',
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        create_btn.callback = on_create
        self.add_item(create_btn)

        skip_btn = discord.ui.Button(
            label="Skip",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        skip_btn.callback = on_skip
        self.add_item(skip_btn)

    async def _advance(self, interaction: discord.Interaction):
        self.step += 1
        if self.step >= len(STEPS):
            await interaction.response.edit_message(
                content="✅ **Setup complete!** Use `/setup-status` to review all mappings.",
                view=None,
            )
            self.stop()
        else:
            self._build()
            await interaction.response.edit_message(
                content=_step_prompt(self.step),
                view=self,
            )


def _step_prompt(step: int) -> str:
    key, label, kind, default = STEPS[step]
    total = len(STEPS)
    kind_hint = {
        "role":         "a server role",
        "category":     "a channel category",
        "text_channel": "a text channel",
        "forum":        "a Forum channel",
    }[kind]
    return (
        f"**Setup — Step {step+1}/{total}**\n"
        f"Select {kind_hint} to use for **{label}**, "
        f'or click **Create "{default}"** to make a new one, '
        f"or **Skip** to configure later."
    )


async def _create_resource(guild: discord.Guild, kind: str, name: str) -> int | None:
    try:
        if kind == "role":
            obj = await guild.create_role(name=name, reason="ThunderWolf /setup")
        elif kind == "category":
            obj = await guild.create_category(name=name, reason="ThunderWolf /setup")
        elif kind == "forum":
            obj = await guild.create_forum(name=name, reason="ThunderWolf /setup")
        else:
            obj = await guild.create_text_channel(name=name, reason="ThunderWolf /setup")
        return obj.id
    except discord.Forbidden:
        return None


# ── setup-status helpers ──────────────────────────────────────────────────────

def _status_lines(guild: discord.Guild, cfg: dict[str, str]) -> str:
    lines = ["**ThunderWolf Setup Status**\n"]
    for key, label, kind, _ in STEPS:
        raw_id = cfg.get(key)
        if not raw_id:
            lines.append(f"❌  {label}")
            continue
        obj = None
        if kind == "role":
            obj = guild.get_role(int(raw_id))
        else:
            obj = guild.get_channel(int(raw_id))
        if obj:
            lines.append(f"✅  {label} → **{obj.name}**")
        else:
            lines.append(f"⚠️  {label} → *(id {raw_id} not found)*")
    return "\n".join(lines)


def _is_ceo_or_owner(interaction: discord.Interaction) -> bool:
    """Allow server owner to bypass the CEO role check (needed on first-run)."""
    if interaction.user.id == interaction.guild.owner_id:
        return True
    ceo_role = discord.utils.get(interaction.guild.roles, name=ROLE_CEO)
    return bool(ceo_role and ceo_role in interaction.user.roles)


# ── cog ───────────────────────────────────────────────────────────────────────

class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="setup",
        description="(CEO / server owner) Configure roles and channels for ThunderWolf.",
    )
    async def setup(self, interaction: discord.Interaction):
        if not _is_ceo_or_owner(interaction):
            await interaction.response.send_message(
                "❌ Only the CEO or server owner can run `/setup`.", ephemeral=True
            )
            return
        view = SetupView(interaction.guild, step=0)
        await interaction.response.send_message(
            _step_prompt(0),
            view=view,
            ephemeral=True,
        )

    @app_commands.command(
        name="setup-status",
        description="Show current ThunderWolf configuration (CEO / Team Manager only).",
    )
    @app_commands.checks.has_any_role(ROLE_CEO, ROLE_TEAM_MANAGER)
    async def setup_status(self, interaction: discord.Interaction):
        cfg = db.get_all_config(interaction.guild_id)
        text = _status_lines(interaction.guild, cfg)
        await interaction.response.send_message(text, ephemeral=True)

    @setup_status.error
    async def setup_status_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingAnyRole):
            await interaction.response.send_message(
                "❌ Only CEO or Team Manager can view setup status.", ephemeral=True
            )
        else:
            raise error

    @app_commands.command(
        name="setup-assign-drivers",
        description="(CEO / server owner) Assign the Driver role to all members who have no welcome role.",
    )
    async def setup_assign_drivers(self, interaction: discord.Interaction):
        if not _is_ceo_or_owner(interaction):
            await interaction.response.send_message(
                "❌ Only the CEO or server owner can run this command.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        driver_role = _resolve_role(guild, CFG_ROLE_DRIVER, ROLE_DRIVER)
        if driver_role is None:
            await interaction.followup.send(
                "❌ Driver role not found. Run `/setup` first to link it.", ephemeral=True
            )
            return

        # Resolve all 5 welcome roles so we can check membership
        welcome_roles = [
            r for _, fallback in _WELCOME_ROLES
            if (r := _resolve_role(guild, _, fallback)) is not None
        ]

        assigned = 0
        skipped  = 0
        errors   = 0

        for member in guild.members:
            if member.bot:
                continue
            has_welcome_role = any(r in member.roles for r in welcome_roles)
            if has_welcome_role:
                skipped += 1
                continue
            try:
                await member.add_roles(driver_role, reason="setup-assign-drivers: no welcome role")
                assigned += 1
            except discord.Forbidden:
                errors += 1

        parts = [f"✅ Assigned **Driver** role to **{assigned}** member(s)."]
        if skipped:
            parts.append(f"{skipped} already had a welcome role and were skipped.")
        if errors:
            parts.append(f"⚠️ {errors} member(s) could not be assigned (role hierarchy issue?).")
        await interaction.followup.send(" ".join(parts), ephemeral=True)

    @app_commands.command(
        name="setup-lock-channels",
        description="(CEO / server owner) Make all channels invisible without a welcome role.",
    )
    async def setup_lock_channels(self, interaction: discord.Interaction):
        if not _is_ceo_or_owner(interaction):
            await interaction.response.send_message(
                "❌ Only the CEO or server owner can run this command.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        # Deny view_channel for @everyone
        everyone_perms = guild.default_role.permissions
        everyone_perms.update(view_channel=False)
        try:
            await guild.default_role.edit(
                permissions=everyone_perms,
                reason="setup-lock-channels: deny view_channel for @everyone",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Missing permission to edit the @everyone role.", ephemeral=True
            )
            return

        # Grant view_channel + read/write access to each welcome role
        updated = []
        missing = []
        for cfg_key, fallback in _WELCOME_ROLES:
            role = _resolve_role(guild, cfg_key, fallback)
            if role is None:
                missing.append(fallback)
                continue
            perms = role.permissions
            perms.update(
                view_channel=True,
                read_messages=True,
                read_message_history=True,
                send_messages=True,
            )
            try:
                await role.edit(
                    permissions=perms,
                    reason="setup-lock-channels: grant read/write access",
                )
                updated.append(role.name)
            except discord.Forbidden:
                missing.append(role.name)

        parts = [
            "🔒 **Server lockdown applied.**",
            "@everyone can no longer see channels.",
            f"✅ Granted read/write access to: {', '.join(f'**{r}**' for r in updated)}." if updated else "",
        ]
        if missing:
            parts.append(
                f"⚠️ Could not update: {', '.join(missing)} — run `/setup` to link them first."
            )
        parts.append(
            "\n_Note: existing channel-level permission overwrites still apply on top of this._"
        )
        await interaction.followup.send("\n".join(p for p in parts if p), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))
