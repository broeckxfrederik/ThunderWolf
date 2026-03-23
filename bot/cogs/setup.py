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
from config import (
    ROLE_DRIVER, ROLE_ENGINEER, ROLE_LIVERY, ROLE_VISITOR, ROLE_UPDATES,
    ROLE_CEO, ROLE_TEAM_MANAGER,
    WELCOME_CATEGORY, RACES_CATEGORY,
    CHANNEL_ROLE_REQUESTS, CHANNEL_CAR_SETUPS, CHANNEL_LINEUP,
    CFG_ROLE_DRIVER, CFG_ROLE_ENGINEER, CFG_ROLE_LIVERY,
    CFG_ROLE_VISITOR, CFG_ROLE_UPDATES, CFG_ROLE_CEO, CFG_ROLE_TM,
    CFG_CAT_WELCOME, CFG_CAT_RACES,
    CFG_CH_ROLE_REQ, CFG_CH_CAR_SETUPS, CFG_CH_LINEUP,
)


# ── wizard step definitions ────────────────────────────────────────────────────

# (config_key, label, kind, default_name)
# kind: "role" | "category" | "text_channel" | "forum"
STEPS: list[tuple[str, str, str, str]] = [
    (CFG_ROLE_DRIVER,    "Driver role",               "role",         ROLE_DRIVER),
    (CFG_ROLE_ENGINEER,  "Engineer role",              "role",         ROLE_ENGINEER),
    (CFG_ROLE_LIVERY,    "Livery Designer role",       "role",         ROLE_LIVERY),
    (CFG_ROLE_VISITOR,   "Visitor role",               "role",         ROLE_VISITOR),
    (CFG_ROLE_UPDATES,   "Updates-Only role",          "role",         ROLE_UPDATES),
    (CFG_ROLE_CEO,       "CEO role",                   "role",         ROLE_CEO),
    (CFG_ROLE_TM,        "Team Manager role",          "role",         ROLE_TEAM_MANAGER),
    (CFG_CAT_WELCOME,    "Welcome category",           "category",     WELCOME_CATEGORY),
    (CFG_CAT_RACES,      "Races category",             "category",     RACES_CATEGORY),
    (CFG_CH_ROLE_REQ,    "Role-requests channel",      "text_channel", CHANNEL_ROLE_REQUESTS),
    (CFG_CH_CAR_SETUPS,  "Car-setups forum channel",   "forum",        CHANNEL_CAR_SETUPS),
    (CFG_CH_LINEUP,      "Team-manager lineups channel","text_channel", CHANNEL_LINEUP),
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

        # Capture references for the closures below
        view       = self
        cfg_key    = key
        def_name   = default_name
        step_kind  = kind

        async def on_select(interaction: discord.Interaction):
            selected_id = str(sel.values[0].id)
            db.set_config(view.guild.id, cfg_key, selected_id)
            await view._advance(interaction)

        async def on_create(interaction: discord.Interaction):
            """Create the resource with the default name and save its ID."""
            created_id = await _create_resource(view.guild, step_kind, def_name)
            if created_id:
                db.set_config(view.guild.id, cfg_key, str(created_id))
            await view._advance(interaction)

        async def on_skip(interaction: discord.Interaction):
            await view._advance(interaction)

        sel.callback = on_select
        self.add_item(sel)

        create_btn = discord.ui.Button(
            label=f"Create "{default_name}"",
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
            _, label, _, _ = STEPS[self.step]
            await interaction.response.edit_message(
                content=_step_prompt(self.step),
                view=self,
            )


def _step_prompt(step: int) -> str:
    key, label, kind, default = STEPS[step]
    total = len(STEPS)
    kind_hint = {
        "role": "a server role",
        "category": "a channel category",
        "text_channel": "a text channel",
        "forum": "a Forum channel",
    }[kind]
    return (
        f"**Setup — Step {step+1}/{total}**\n"
        f"Select {kind_hint} to use for **{label}**, "
        f"or click **Create \"{default}\"** to make a new one, "
        f"or **Skip** to configure later."
    )


async def _create_resource(guild: discord.Guild, kind: str, name: str) -> int | None:
    """Create a Discord role/channel with the given name. Returns the new ID or None."""
    try:
        if kind == "role":
            obj = await guild.create_role(name=name, reason="ThunderWolf /setup")
        elif kind == "category":
            obj = await guild.create_category(name=name, reason="ThunderWolf /setup")
        elif kind == "forum":
            obj = await guild.create_forum(name=name, reason="ThunderWolf /setup")
        else:  # text_channel
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
        # Try to resolve so we can show a name
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


# ── cog ───────────────────────────────────────────────────────────────────────

class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="setup",
        description="(CEO only) Configure roles and channels for ThunderWolf.",
    )
    @app_commands.checks.has_any_role(ROLE_CEO)
    async def setup(self, interaction: discord.Interaction):
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

    @setup.error
    async def setup_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingAnyRole):
            await interaction.response.send_message(
                "❌ Only the CEO can run `/setup`.", ephemeral=True
            )
        else:
            raise error

    @setup_status.error
    async def setup_status_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingAnyRole):
            await interaction.response.send_message(
                "❌ Only CEO or Team Manager can view setup status.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))
