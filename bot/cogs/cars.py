"""
Cars cog
────────
Manages the guild's car list — the single source of truth for car names.

Commands (CEO / Team Manager only):
  /car-add name:…    — add a car; creates its setup forum thread immediately
  /car-remove name:… — remove a car from the list (thread is kept)
  /car-list          — show all registered cars

The car list is persisted in SQLite (cars table) and is used by:
  • /event  — autocomplete so TM always picks a canonical name
  • setup threads — one permanent forum thread per car, never duplicated
"""

import discord
from discord import app_commands
from discord.ext import commands

import db
from config import (
    ROLE_CEO, ROLE_TEAM_MANAGER,
    CFG_CH_CAR_SETUPS, CHANNEL_CAR_SETUPS,
)

SETUP_TEMPLATE = (
    "## {car} — Setup Thread\n\n"
    "Use this thread to share and discuss setups for the **{car}**.\n\n"
    "### Template\n"
    "```\n"
    "Tyres      : \n"
    "Aero       : \n"
    "Suspension : \n"
    "Diff       : \n"
    "Brake bias : \n"
    "Notes      : \n"
    "```\n"
)


async def _get_or_create_setup_forum(guild: discord.Guild) -> discord.ForumChannel | None:
    """Return the car-setups forum channel, using the DB config or falling back to name."""
    raw_id = db.get_config(guild.id, CFG_CH_CAR_SETUPS)
    if raw_id:
        ch = guild.get_channel(int(raw_id))
        if isinstance(ch, discord.ForumChannel):
            return ch

    # Fallback: search by default name
    ch = discord.utils.get(guild.forums, name=CHANNEL_CAR_SETUPS)
    if ch:
        db.set_config(guild.id, CFG_CH_CAR_SETUPS, str(ch.id))
        return ch

    return None


class Cars(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /car-add ──────────────────────────────────────────────────────────────

    @app_commands.command(name="car-add", description="Add a car to the guild car list.")
    @app_commands.describe(name="Exact car name (e.g. BMW M4 GT3)")
    @app_commands.checks.has_any_role(ROLE_CEO, ROLE_TEAM_MANAGER)
    async def car_add(self, interaction: discord.Interaction, name: str):
        name = name.strip()
        if not name:
            await interaction.response.send_message("❌ Car name cannot be empty.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        car_id = db.add_car(interaction.guild_id, name)

        # Create setup forum thread if not already linked
        existing = db.get_car_by_name(interaction.guild_id, name)
        thread_mention = ""

        if not existing or not existing.get("setup_thread_id"):
            forum = await _get_or_create_setup_forum(interaction.guild)
            if forum:
                thread, _ = await forum.create_thread(
                    name=f"{name} — Setup",
                    content=SETUP_TEMPLATE.format(car=name),
                    reason=f"Car setup thread for {name}",
                )
                db.set_car_thread(car_id, thread.id)
                thread_mention = f"\nSetup thread: {thread.mention}"
            else:
                thread_mention = (
                    "\n⚠️ No car-setups forum found — run `/setup` to link one "
                    "and the thread will be created next time."
                )

        await interaction.followup.send(
            f"✅ **{name}** added to the car list.{thread_mention}", ephemeral=True
        )

    # ── /car-remove ───────────────────────────────────────────────────────────

    @app_commands.command(name="car-remove", description="Remove a car from the guild car list.")
    @app_commands.describe(name="Car name to remove")
    @app_commands.checks.has_any_role(ROLE_CEO, ROLE_TEAM_MANAGER)
    async def car_remove(self, interaction: discord.Interaction, name: str):
        removed = db.remove_car(interaction.guild_id, name.strip())
        if removed:
            await interaction.response.send_message(
                f"✅ **{name}** removed. (Its setup thread is kept.)", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ **{name}** not found in the car list.", ephemeral=True
            )

    @car_remove.autocomplete("name")
    async def car_remove_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        cars = db.search_cars(interaction.guild_id, current)
        return [app_commands.Choice(name=c["name"], value=c["name"]) for c in cars]

    # ── /car-list ─────────────────────────────────────────────────────────────

    @app_commands.command(name="car-list", description="Show all registered cars.")
    async def car_list(self, interaction: discord.Interaction):
        cars = db.list_cars(interaction.guild_id)
        if not cars:
            await interaction.response.send_message(
                "No cars registered yet. Use `/car-add` to add one.", ephemeral=True
            )
            return

        lines = ["**Registered Cars**\n"]
        for c in cars:
            thread_id = c.get("setup_thread_id")
            thread_str = f" · <#{thread_id}>" if thread_id else " · *(no setup thread)*"
            lines.append(f"🏎️ **{c['name']}**{thread_str}")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # ── error handlers ────────────────────────────────────────────────────────

    @car_add.error
    @car_remove.error
    async def cars_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingAnyRole):
            await interaction.response.send_message(
                "❌ Only CEO or Team Manager can manage the car list.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Cars(bot))
