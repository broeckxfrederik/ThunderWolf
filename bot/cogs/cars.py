"""
Cars cog
────────
Manages the guild's car list — the single source of truth for car names.

Commands (CEO / Team Manager only):
  /car-add name:…    — add a car to the list
  /car-remove name:… — remove a car from the list
  /car-list          — show all registered cars

The car list is persisted in SQLite (cars table) and is used by:
  • /event  — autocomplete so TM always picks a canonical name
"""

import discord
from discord import app_commands
from discord.ext import commands

import db
from config import ROLE_CEO, ROLE_TEAM_MANAGER


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

        db.add_car(interaction.guild_id, name)
        await interaction.response.send_message(f"✅ **{name}** added to the car list.", ephemeral=True)

    # ── /car-remove ───────────────────────────────────────────────────────────

    @app_commands.command(name="car-remove", description="Remove a car from the guild car list.")
    @app_commands.describe(name="Car name to remove")
    @app_commands.checks.has_any_role(ROLE_CEO, ROLE_TEAM_MANAGER)
    async def car_remove(self, interaction: discord.Interaction, name: str):
        removed = db.remove_car(interaction.guild_id, name.strip())
        if removed:
            await interaction.response.send_message(f"✅ **{name}** removed.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ **{name}** not found in the car list.", ephemeral=True)

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

        lines = ["**Registered Cars**\n"] + [f"🏎️ **{c['name']}**" for c in cars]
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
