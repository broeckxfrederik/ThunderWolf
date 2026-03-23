"""
Race Event cog
──────────────
/event name:… date:YYYY-MM-DD time:HH:MM cars:…
    Creates a dedicated #race-<name> channel under the Races category.
    Car names use autocomplete from the guild car list (see /car-add).
    Duplicate car names in the list create multiple slots (BMW #1, BMW #2, …).

    Lineup flow:
      1. Drivers click a car-slot button → registered for that slot.
      2. TM reviews the draft and clicks ✅ Confirm Lineup.
      3. Lineup locked, pinned embed updated, buttons disabled.
      4. TM can override before confirm via /lineup-set.

    Reminders:
      Background task fires T-24h and T-1h pings in the race channel.

/lineup-set driver:@user slot:…
    Override a driver's slot before the lineup is confirmed (TM only).

/event-result
    Post a results embed in the current race channel (TM only).
"""

import datetime
import json

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
from config import (
    ROLE_CEO, ROLE_TEAM_MANAGER,
    CFG_CAT_RACES, RACES_CATEGORY,
    RACE_ROLE_PREFIX,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _slot_key(car_id: int, slot_num: int) -> str:
    return f"{car_id}_{slot_num}"


def _slot_label(car_name: str, count: int, slot_num: int) -> str:
    return f"{car_name} #{slot_num}" if count > 1 else car_name


def _build_slots(guild_id: int, car_names: list[str]) -> list[dict]:
    """
    Convert a list of car names (with possible duplicates) into slot dicts.
    Each slot: {car_id, car_name, slot_num, label}
    """
    # count occurrences of each name
    name_counts: dict[str, int] = {}
    for n in car_names:
        name_counts[n] = name_counts.get(n, 0) + 1

    name_seen: dict[str, int] = {}
    slots = []
    for name in car_names:
        car = db.get_car_by_name(guild_id, name)
        if car is None:
            # Auto-add unknown cars so autocomplete misses don't break the flow
            car_id = db.add_car(guild_id, name)
            car = {"id": car_id, "name": name}

        name_seen[name] = name_seen.get(name, 0) + 1
        snum  = name_seen[name]
        total = name_counts[name]
        slots.append({
            "car_id":   car["id"],
            "car_name": name,
            "slot_num": snum,
            "label":    _slot_label(name, total, snum),
        })
    return slots


async def _get_or_create_races_category(guild: discord.Guild) -> discord.CategoryChannel:
    raw_id = db.get_config(guild.id, CFG_CAT_RACES)
    if raw_id:
        cat = guild.get_channel(int(raw_id))
        if isinstance(cat, discord.CategoryChannel):
            return cat

    cat = discord.utils.get(guild.categories, name=RACES_CATEGORY)
    if cat:
        return cat

    cat = await guild.create_category(name=RACES_CATEGORY, reason="Race channels category")
    db.set_config(guild.id, CFG_CAT_RACES, str(cat.id))
    return cat


async def _cleanup_race_roles(guild: discord.Guild, event_id: int) -> None:
    """Remove Race-* roles from every driver registered in the given event."""
    event = db.get_event(event_id)
    if not event:
        return
    lineup: dict = event["lineup"]
    slots:  list = event["slots"]

    # Collect the distinct car names used in this event
    car_names = {s["car_name"] for s in slots}

    for member_id in lineup.values():
        member = guild.get_member(int(member_id))
        if not member:
            continue
        roles_to_remove = [
            r for r in member.roles
            if r.name.startswith(RACE_ROLE_PREFIX)
            and r.name[len(RACE_ROLE_PREFIX):] in car_names
        ]
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="Race event concluded")


def _lineup_embed(event: dict, guild: discord.Guild) -> discord.Embed:
    date_str = event["date_utc"]
    confirmed = event.get("confirmed", 0)

    embed = discord.Embed(
        title=f"🏁 {event['name']}",
        description=f"📅 {date_str} UTC",
        colour=discord.Colour.green() if confirmed else discord.Colour.orange(),
    )
    embed.set_footer(text="✅ Lineup confirmed" if confirmed else "⏳ Lineup pending TM confirmation")

    lineup: dict = event["lineup"]
    slots:  list = event["slots"]

    for slot in slots:
        key    = _slot_key(slot["car_id"], slot["slot_num"])
        mid    = lineup.get(key)
        member = guild.get_member(int(mid)) if mid else None
        driver = member.display_name if member else "*open*"
        embed.add_field(name=slot["label"], value=driver, inline=True)

    return embed


# ── views ─────────────────────────────────────────────────────────────────────

class LineupView(discord.ui.View):
    """Car-slot buttons + TM confirm. Rebuilt from DB on bot restart via RaceEvent.restore_views()."""

    def __init__(self, event_id: int, slots: list[dict], confirmed: bool = False):
        super().__init__(timeout=None)
        self.event_id  = event_id
        self.confirmed = confirmed

        for slot in slots:
            btn = CarSlotButton(event_id, slot, disabled=confirmed)
            self.add_item(btn)

        confirm_btn = discord.ui.Button(
            label="✅ Confirm Lineup",
            style=discord.ButtonStyle.success,
            custom_id=f"confirm_{event_id}",
            row=4,
            disabled=confirmed,
        )
        confirm_btn.callback = self._confirm_callback
        self.add_item(confirm_btn)

    async def _confirm_callback(self, interaction: discord.Interaction):
        member = interaction.user
        guild  = interaction.guild
        tm_role  = guild.get_role(int(db.get_config(guild.id, "role_tm") or 0))
        ceo_role = guild.get_role(int(db.get_config(guild.id, "role_ceo") or 0))

        has_tm  = tm_role  and tm_role  in member.roles
        has_ceo = ceo_role and ceo_role in member.roles
        if not (has_tm or has_ceo):
            await interaction.response.send_message(
                "❌ Only Team Manager or CEO can confirm the lineup.", ephemeral=True
            )
            return

        db.confirm_event(self.event_id)
        event = db.get_event(self.event_id)

        # Disable all buttons
        for child in self.children:
            child.disabled = True
        self.confirmed = True

        embed = _lineup_embed(event, guild)
        await interaction.response.edit_message(embed=embed, view=self)

        # Post a separate pinned confirmation message
        confirm_msg = await interaction.channel.send(
            f"📌 **Lineup confirmed by {member.display_name}!**"
        )
        try:
            await confirm_msg.pin()
        except discord.Forbidden:
            pass


class CarSlotButton(discord.ui.Button):
    def __init__(self, event_id: int, slot: dict, disabled: bool = False):
        super().__init__(
            label=slot["label"],
            style=discord.ButtonStyle.primary,
            custom_id=f"slot_{event_id}_{slot['car_id']}_{slot['slot_num']}",
            disabled=disabled,
        )
        self.event_id = event_id
        self.slot     = slot

    async def callback(self, interaction: discord.Interaction):
        member = interaction.user
        guild  = interaction.guild

        event = db.get_event(self.event_id)
        if event is None or event["confirmed"]:
            await interaction.response.send_message(
                "❌ This event is no longer active.", ephemeral=True
            )
            return

        lineup = event["lineup"]
        new_key = _slot_key(self.slot["car_id"], self.slot["slot_num"])

        # Remove member from any existing slot
        for k, v in list(lineup.items()):
            if str(v) == str(member.id):
                old_slot = next(
                    (s for s in event["slots"]
                     if _slot_key(s["car_id"], s["slot_num"]) == k),
                    None,
                )
                if old_slot:
                    old_role = discord.utils.get(
                        guild.roles, name=f"{RACE_ROLE_PREFIX}{old_slot['car_name']}"
                    )
                    if old_role and old_role in member.roles:
                        await member.remove_roles(old_role, reason="Changed slot")
                lineup.pop(k)
                break

        # Assign to new slot
        lineup[new_key] = member.id
        db.update_lineup(self.event_id, lineup)

        # Assign race role
        role_name = f"{RACE_ROLE_PREFIX}{self.slot['car_name']}"
        race_role = discord.utils.get(guild.roles, name=role_name)
        if race_role is None:
            race_role = await guild.create_role(name=role_name, reason="Race event role")
        await member.add_roles(race_role, reason=f"Registered for {self.slot['label']}")

        # Update the lineup embed in-place
        embed = _lineup_embed(db.get_event(self.event_id), guild)
        await interaction.response.edit_message(embed=embed, view=self.view)


# ── results modal ─────────────────────────────────────────────────────────────

class ResultsModal(discord.ui.Modal, title="Post Race Results"):
    positions = discord.ui.TextInput(
        label="Final standings",
        style=discord.TextStyle.paragraph,
        placeholder="1. Driver Name – Car\n2. Driver Name – Car\n…",
        required=True,
    )
    notes = discord.ui.TextInput(
        label="Notes (fastest lap, incidents, etc.)",
        style=discord.TextStyle.paragraph,
        required=False,
    )

    def __init__(self, event_id: int):
        super().__init__()
        self.event_id = event_id

    async def on_submit(self, interaction: discord.Interaction):
        import datetime as _dt
        results = {
            "positions": self.positions.value,
            "notes":     self.notes.value or "",
        }
        db.set_results(self.event_id, results, _dt.datetime.utcnow().isoformat())

        embed = discord.Embed(
            title="🏆 Race Results",
            colour=discord.Colour.gold(),
        )
        embed.add_field(name="Final Standings", value=self.positions.value, inline=False)
        if self.notes.value:
            embed.add_field(name="Notes", value=self.notes.value, inline=False)
        embed.set_footer(text=f"Posted by {interaction.user.display_name}")

        msg = await interaction.channel.send(embed=embed)
        try:
            await msg.pin()
        except discord.Forbidden:
            pass

        await interaction.response.send_message(
            "✅ Results posted. Race-* roles will be removed 48h after the race.", ephemeral=True
        )


# ── cog ───────────────────────────────────────────────────────────────────────

class RaceEvent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reminder_task.start()

    def cog_unload(self):
        self.reminder_task.cancel()

    async def cog_load(self):
        """Restore LineupViews for all active events on bot (re)start."""
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            for event in db.get_active_events(guild.id):
                if event["confirmed"] or event.get("channel_id") is None:
                    continue
                ch = self.bot.get_channel(event["channel_id"])
                if ch is None:
                    continue
                view = LineupView(event["id"], event["slots"], confirmed=False)
                self.bot.add_view(view)

    # ── /event ────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="event",
        description="Create a race event with a dedicated channel and lineup.",
    )
    @app_commands.describe(
        name="Race name (e.g. Monza Round 1)",
        date="Race date — YYYY-MM-DD",
        time="Race time UTC — HH:MM",
        cars="Comma-separated car names (use autocomplete). Repeat a name for multiple slots.",
    )
    @app_commands.checks.has_any_role(ROLE_CEO, ROLE_TEAM_MANAGER)
    async def event(
        self,
        interaction: discord.Interaction,
        name: str,
        date: str,
        time: str,
        cars: str,
    ):
        guild = interaction.guild

        # Parse and validate date/time
        try:
            dt = datetime.datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid date or time. Use YYYY-MM-DD for date and HH:MM for time.",
                ephemeral=True,
            )
            return

        car_names = [c.strip() for c in cars.split(",") if c.strip()]
        if not car_names:
            await interaction.response.send_message(
                "❌ Please provide at least one car.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        slots     = _build_slots(guild.id, car_names)
        date_str  = dt.strftime("%Y-%m-%d %H:%M")
        event_id  = db.create_event(guild.id, name, date_str, slots)

        # Create race channel
        category = await _get_or_create_races_category(guild)
        safe_name = name.lower().replace(" ", "-")[:80]
        channel = await guild.create_text_channel(
            name=f"race-{safe_name}",
            category=category,
            reason=f"Race event: {name}",
        )
        db.set_event_channel(event_id, channel.id)

        # Post lineup embed + buttons
        event = db.get_event(event_id)
        view  = LineupView(event_id, slots)
        self.bot.add_view(view)

        embed = _lineup_embed(event, guild)
        embed.description = (
            f"📅 **{date_str} UTC**\n\n"
            "Click your car slot to register. You can change your pick anytime until the lineup is confirmed."
        )
        await channel.send(
            f"🏁 **Race Event: {name}**\n"
            f"Organised by {interaction.user.mention}",
            embed=embed,
            view=view,
        )

        await interaction.followup.send(
            f"✅ Race event **{name}** created: {channel.mention}", ephemeral=True
        )

    @event.autocomplete("cars")
    async def cars_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete the last car name in a comma-separated string."""
        parts     = [p.strip() for p in current.split(",")]
        last_part = parts[-1]
        prefix    = ", ".join(parts[:-1])
        if prefix:
            prefix += ", "

        matches = db.search_cars(interaction.guild_id, last_part, limit=10)
        return [
            app_commands.Choice(
                name=prefix + m["name"],
                value=prefix + m["name"],
            )
            for m in matches
        ]

    # ── /lineup-set ───────────────────────────────────────────────────────────

    @app_commands.command(
        name="lineup-set",
        description="Override a driver's slot before the lineup is confirmed (TM / CEO only).",
    )
    @app_commands.describe(
        driver="The driver to place",
        slot="Slot label exactly as shown in the lineup (e.g. 'BMW M4 GT3 #2')",
    )
    @app_commands.checks.has_any_role(ROLE_CEO, ROLE_TEAM_MANAGER)
    async def lineup_set(
        self, interaction: discord.Interaction, driver: discord.Member, slot: str
    ):
        guild = interaction.guild
        # Find the active event for this channel
        event = self._event_for_channel(guild.id, interaction.channel_id)
        if event is None:
            await interaction.response.send_message(
                "❌ No active event found for this channel.", ephemeral=True
            )
            return
        if event["confirmed"]:
            await interaction.response.send_message(
                "❌ Lineup is already confirmed.", ephemeral=True
            )
            return

        target_slot = next(
            (s for s in event["slots"] if s["label"].lower() == slot.strip().lower()), None
        )
        if target_slot is None:
            slot_names = ", ".join(f'"{s["label"]}"' for s in event["slots"])
            await interaction.response.send_message(
                f"❌ Slot not found. Available slots: {slot_names}", ephemeral=True
            )
            return

        lineup  = event["lineup"]
        new_key = _slot_key(target_slot["car_id"], target_slot["slot_num"])

        # Remove driver from any current slot
        for k, v in list(lineup.items()):
            if str(v) == str(driver.id):
                lineup.pop(k)
                break

        lineup[new_key] = driver.id
        db.update_lineup(event["id"], lineup)

        await interaction.response.send_message(
            f"✅ {driver.mention} placed in **{target_slot['label']}**.", ephemeral=True
        )

    # ── /event-result ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="event-result",
        description="Post race results in this channel (TM / CEO only).",
    )
    @app_commands.checks.has_any_role(ROLE_CEO, ROLE_TEAM_MANAGER)
    async def event_result(self, interaction: discord.Interaction):
        event = self._event_for_channel(interaction.guild_id, interaction.channel_id)
        if event is None:
            await interaction.response.send_message(
                "❌ No race event found for this channel.", ephemeral=True
            )
            return
        await interaction.response.send_modal(ResultsModal(event["id"]))

    # ── helpers ───────────────────────────────────────────────────────────────

    def _event_for_channel(self, guild_id: int, channel_id: int) -> dict | None:
        for ev in db.get_active_events(guild_id):
            if ev.get("channel_id") == channel_id:
                return ev
        return None

    # ── background: reminders ─────────────────────────────────────────────────

    @tasks.loop(minutes=15)
    async def reminder_task(self):
        now = datetime.datetime.utcnow()
        for guild in self.bot.guilds:
            for event in db.get_active_events(guild.id):
                if event.get("channel_id") is None:
                    continue
                ch = self.bot.get_channel(event["channel_id"])
                if ch is None:
                    continue

                try:
                    race_dt = datetime.datetime.strptime(event["date_utc"], "%Y-%m-%d %H:%M")
                except ValueError:
                    continue

                delta = race_dt - now
                lineup: dict = event["lineup"]

                # T-24h reminder
                if (
                    not event["reminder_24h_sent"]
                    and datetime.timedelta(hours=23, minutes=45) <= delta <= datetime.timedelta(hours=24, minutes=15)
                ):
                    mentions = self._driver_mentions(guild, lineup)
                    await ch.send(
                        f"⏰ **24-hour reminder!** The race **{event['name']}** starts in ~24h.\n"
                        f"{mentions}"
                    )
                    db.mark_reminder(event["id"], "24h")

                # T-1h reminder
                elif (
                    not event["reminder_1h_sent"]
                    and datetime.timedelta(minutes=45) <= delta <= datetime.timedelta(hours=1, minutes=15)
                ):
                    embed = _lineup_embed(event, guild)
                    mentions = self._driver_mentions(guild, lineup)
                    await ch.send(
                        f"🚨 **1-hour reminder!** The race **{event['name']}** starts soon!\n"
                        f"{mentions}",
                        embed=embed,
                    )
                    db.mark_reminder(event["id"], "1h")

        # 48h post-results: clean up Race-* roles
        cleanup_cutoff = (now - datetime.timedelta(hours=48)).isoformat()
        for event in db.get_events_due_cleanup(cleanup_cutoff):
            guild = self.bot.get_guild(event["guild_id"])
            if guild:
                await _cleanup_race_roles(guild, event["id"])
                db.mark_roles_cleaned(event["id"])

    def _driver_mentions(self, guild: discord.Guild, lineup: dict) -> str:
        mentions = []
        for member_id in lineup.values():
            m = guild.get_member(int(member_id))
            if m:
                mentions.append(m.mention)
        return " ".join(mentions) if mentions else "*No drivers registered yet.*"

    @reminder_task.before_loop
    async def before_reminder(self):
        await self.bot.wait_until_ready()

    # ── error handlers ────────────────────────────────────────────────────────

    @event.error
    @lineup_set.error
    @event_result.error
    async def event_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingAnyRole):
            await interaction.response.send_message(
                "❌ Only CEO or Team Manager can use this command.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(RaceEvent(bot))
