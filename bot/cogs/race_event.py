"""
Race Event cog
──────────────
• /event <cars>  — starts a new race event.
  <cars> is a comma-separated list of car/team names, e.g.:
      /event cars:Ferrari,Mercedes,Red Bull

  What happens:
  1. All previous Race-* temp roles are removed from every member.
  2. A message is posted in the current channel with one button per car.
     Racers click a button to register for that car.
  3. Each pick:
     - Assigns a temp role  Race-<Car>  to the member.
     - Updates the live lineup summary in #team-manager-lineups.
  4. A member can change their pick by clicking a different car button
     (old temp role removed, new one assigned).
"""

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    CHANNEL_LINEUP, RACE_ROLE_PREFIX,
    ROLE_CEO, ROLE_TEAM_MANAGER, ROLE_RACER,
)


# ── state ─────────────────────────────────────────────────────────────────────
# member_id → car name  (current event only; cleared on next /event)
_picks: dict[int, str] = {}

# The lineup summary message posted in the TM channel (we edit it on each pick)
_lineup_msg: discord.Message | None = None


# ── car selection view ────────────────────────────────────────────────────────

class CarView(discord.ui.View):
    """Persistent view with one button per car."""

    def __init__(self, cars: list[str], cog: "RaceEvent"):
        super().__init__(timeout=None)  # stays active until next /event
        self.cog = cog
        for car in cars:
            self.add_item(CarButton(car))


class CarButton(discord.ui.Button):
    def __init__(self, car: str):
        super().__init__(
            label=car,
            style=discord.ButtonStyle.primary,
            custom_id=f"car_{car}",
        )
        self.car = car

    async def callback(self, interaction: discord.Interaction):
        member = interaction.user
        guild  = interaction.guild

        # Remove old Race-* role if the member already picked
        old_car = _picks.get(member.id)
        if old_car:
            old_role = discord.utils.get(guild.roles, name=f"{RACE_ROLE_PREFIX}{old_car}")
            if old_role and old_role in member.roles:
                await member.remove_roles(old_role, reason="Changed car pick")

        # Assign new Race-<Car> role (create it if it doesn't exist)
        role_name = f"{RACE_ROLE_PREFIX}{self.car}"
        new_role = discord.utils.get(guild.roles, name=role_name)
        if new_role is None:
            new_role = await guild.create_role(name=role_name, reason="Race event temp role")
        await member.add_roles(new_role, reason="Car selection")

        _picks[member.id] = self.car

        await interaction.response.send_message(
            f"✅ You're registered for **{self.car}**!", ephemeral=True
        )

        # Update the TM lineup message
        await self.view.cog.update_lineup(guild)


# ── cog ───────────────────────────────────────────────────────────────────────

class RaceEvent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /event command ────────────────────────────────────────────────────────

    @app_commands.command(
        name="event",
        description="Start a race event. Provide a comma-separated list of cars.",
    )
    @app_commands.describe(cars="Comma-separated car/team names, e.g. Ferrari,Mercedes,Red Bull")
    @app_commands.checks.has_any_role(ROLE_CEO, ROLE_TEAM_MANAGER)
    async def event(self, interaction: discord.Interaction, cars: str):
        guild = interaction.guild
        car_list = [c.strip() for c in cars.split(",") if c.strip()]

        if not car_list:
            await interaction.response.send_message("❌ Please provide at least one car.", ephemeral=True)
            return

        await interaction.response.defer()

        # 1. Wipe all previous Race-* temp roles from every member
        await self._clear_race_roles(guild)
        _picks.clear()

        # 2. Post car selection message
        view = CarView(car_list, self)
        cars_display = " | ".join(f"**{c}**" for c in car_list)
        await interaction.followup.send(
            f"🏁 **Race event started!**\n\n"
            f"Available cars: {cars_display}\n\n"
            f"Click your car to register. You can change your pick anytime.",
            view=view,
        )

        # 3. Announce in TM channel and post initial (empty) lineup
        global _lineup_msg
        _lineup_msg = None
        await self.update_lineup(guild, car_list=car_list)

    # ── helpers ───────────────────────────────────────────────────────────────

    async def _clear_race_roles(self, guild: discord.Guild):
        """Remove every Race-* role from all members and delete the roles."""
        race_roles = [r for r in guild.roles if r.name.startswith(RACE_ROLE_PREFIX)]
        for role in race_roles:
            for member in role.members:
                await member.remove_roles(role, reason="New race event started")
            await role.delete(reason="New race event started")

    async def _get_lineup_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        ch = discord.utils.get(guild.text_channels, name=CHANNEL_LINEUP)
        if ch is None:
            # Try to create it (CEO + TM only)
            ceo_role = discord.utils.get(guild.roles, name=ROLE_CEO)
            tm_role  = discord.utils.get(guild.roles, name=ROLE_TEAM_MANAGER)
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
            }
            if ceo_role:
                overwrites[ceo_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
            if tm_role:
                overwrites[tm_role]  = discord.PermissionOverwrite(view_channel=True, send_messages=True)
            ch = await guild.create_text_channel(
                CHANNEL_LINEUP, overwrites=overwrites, reason="Race lineup channel"
            )
        return ch

    async def update_lineup(self, guild: discord.Guild, car_list: list[str] | None = None):
        """Edit (or create) the pinned lineup summary in the TM channel."""
        global _lineup_msg

        ch = await self._get_lineup_channel(guild)
        if ch is None:
            return

        # Build lineup text
        # Group picks by car
        by_car: dict[str, list[str]] = {}
        for member_id, car in _picks.items():
            member = guild.get_member(member_id)
            name = member.display_name if member else f"<{member_id}>"
            by_car.setdefault(car, []).append(name)

        lines = ["📋 **Current Race Lineup**\n"]
        if car_list is None:
            # Derive from existing picks + any cars already in the message
            known_cars = list(by_car.keys())
        else:
            known_cars = car_list

        if not known_cars and not by_car:
            lines.append("_No picks yet._")
        else:
            all_cars = known_cars if known_cars else list(by_car.keys())
            for car in all_cars:
                drivers = by_car.get(car, [])
                driver_str = ", ".join(drivers) if drivers else "_no one yet_"
                lines.append(f"🏎️ **{car}**: {driver_str}")

        content = "\n".join(lines)

        if _lineup_msg is None:
            _lineup_msg = await ch.send(content)
            try:
                await _lineup_msg.pin()
            except discord.Forbidden:
                pass
        else:
            try:
                await _lineup_msg.edit(content=content)
            except discord.NotFound:
                _lineup_msg = await ch.send(content)

    # ── error handler ─────────────────────────────────────────────────────────

    @event.error
    async def event_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingAnyRole):
            await interaction.response.send_message(
                "❌ Only CEO or Team Manager can start a race event.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(RaceEvent(bot))
