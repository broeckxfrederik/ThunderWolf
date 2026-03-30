"""
Race Event cog
──────────────
/event name:… date:YYYY-MM-DD time:HH:MM car1:… [car2..car6]
    Creates a dedicated #race-<name> channel (visible to everyone).
    Posts the lineup embed + controls in both the race channel and
    #team-manager-lineups.  Both messages stay in sync on every change.
    Tags the Driver-Notification role on creation.

    Lineup controls (on the embed):
      • Dropdown "Select your slot" — drivers pick / switch their slot
      • 🚪 Withdraw              — remove yourself from the lineup
      • ✅ Confirm Lineup        — TM/CEO locks the lineup

    Background tasks:
      • T-24h and T-1h reminders pinged in the race channel
      • At race start: channel restricted to confirmed drivers + CEO/TM
      • 48h after race: Race-* roles removed

/lineup-set driver:@user slot:…   Override a driver's slot (TM/CEO, autocomplete)
/lineup-remove driver:@user       Remove a driver from the lineup (TM/CEO)
/event-result                     Post results embed in the race channel (TM/CEO)
"""

import asyncio
import datetime
import json

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
from utils import resolve_role as _resolve_cfg_role
from config import (
    ROLE_CEO, ROLE_TEAM_MANAGER, ROLE_DRIVER,
    CFG_CAT_RACES, RACES_CATEGORY,
    CFG_CH_LINEUP, CHANNEL_LINEUP,
    CFG_ROLE_CEO, CFG_ROLE_TM, CFG_ROLE_DRIVER,
    RACE_ROLE_PREFIX,
    ROLE_DRIVER_NOTIF,
)


# ── race-condition guard ───────────────────────────────────────────────────────

_lineup_locks: dict[int, asyncio.Lock] = {}


def _get_lineup_lock(event_id: int) -> asyncio.Lock:
    if event_id not in _lineup_locks:
        _lineup_locks[event_id] = asyncio.Lock()
    return _lineup_locks[event_id]


# ── helpers ───────────────────────────────────────────────────────────────────

def _slot_key(car_id: int, slot_num: int) -> str:
    return f"{car_id}_{slot_num}"


def _slot_label(car_name: str, count: int, slot_num: int) -> str:
    return f"{car_name} #{slot_num}" if count > 1 else car_name


def _normalize_lineup(lineup: dict) -> dict:
    """Ensure lineup values are always lists of int member IDs (backward compat)."""
    normalized = {}
    for k, v in lineup.items():
        if isinstance(v, list):
            normalized[k] = [int(x) for x in v]
        else:
            normalized[k] = [int(v)]
    return normalized


def _build_slots(guild_id: int, car_names: list[str]) -> list[dict]:
    """
    Convert a list of car names (with possible duplicates) into slot dicts.
    Each slot: {car_id, car_name, slot_num, label}
    Raises ValueError listing unknown names if any car is not in the DB.
    """
    unknown = [n for n in car_names if db.get_car_by_name(guild_id, n) is None]
    if unknown:
        known = [c["name"] for c in db.list_cars(guild_id)]
        known_str = ", ".join(f"**{n}**" for n in known) if known else "*(none yet — use /car-add)*"
        raise ValueError(
            f"Unknown car(s): {', '.join(unknown)}\nRegistered cars: {known_str}"
        )

    name_counts: dict[str, int] = {}
    for n in car_names:
        name_counts[n] = name_counts.get(n, 0) + 1

    name_seen: dict[str, int] = {}
    slots = []
    for name in car_names:
        car = db.get_car_by_name(guild_id, name)
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


async def _get_or_create_lineup_channel(guild: discord.Guild) -> discord.TextChannel | None:
    """Return the #team-manager-lineups channel, creating it if needed."""
    raw_id = db.get_config(guild.id, CFG_CH_LINEUP)
    if raw_id:
        ch = guild.get_channel(int(raw_id))
        if isinstance(ch, discord.TextChannel):
            return ch

    ch = discord.utils.get(guild.text_channels, name=CHANNEL_LINEUP)
    if ch:
        db.set_config(guild.id, CFG_CH_LINEUP, str(ch.id))
        return ch

    # Create restricted to CEO/TM only
    ceo_role = _resolve_cfg_role(guild, CFG_ROLE_CEO, ROLE_CEO)
    tm_role  = _resolve_cfg_role(guild, CFG_ROLE_TM,  ROLE_TEAM_MANAGER)
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
            CHANNEL_LINEUP,
            overwrites=overwrites,
            reason="Team manager lineups channel",
        )
        db.set_config(guild.id, CFG_CH_LINEUP, str(ch.id))
        return ch
    except discord.Forbidden:
        return None


async def _cleanup_race_roles(guild: discord.Guild, event_id: int) -> None:
    """Remove Race-* roles from every driver registered in the given event."""
    event = db.get_event(event_id)
    if not event:
        return
    lineup = _normalize_lineup(event["lineup"])
    slots:  list = event["slots"]

    car_names = {s["car_name"] for s in slots}

    # Flatten to a set of all member IDs
    all_member_ids: set[int] = set()
    for occupants in lineup.values():
        all_member_ids.update(occupants)

    for member_id in all_member_ids:
        member = guild.get_member(member_id)
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
    date_str  = event["date_utc"].replace("T", " ")
    confirmed = event.get("confirmed", 0)

    embed = discord.Embed(
        title=f"🏁 {event['name']}",
        description=f"📅 {date_str} UTC",
        colour=discord.Colour.green() if confirmed else discord.Colour.orange(),
    )
    embed.set_footer(text="✅ Lineup confirmed" if confirmed else "⏳ Lineup pending TM confirmation")

    lineup = _normalize_lineup(event["lineup"])
    slots:  list = event["slots"]

    for slot in slots:
        key      = _slot_key(slot["car_id"], slot["slot_num"])
        occupants = lineup.get(key, [])
        if occupants:
            names = []
            for mid in occupants:
                m = guild.get_member(mid)
                names.append(m.display_name if m else f"<{mid}>")
            driver = ", ".join(names)
        else:
            driver = "*open*"
        embed.add_field(name=slot["label"], value=driver, inline=True)

    return embed


async def _sync_other_message(
    bot: discord.Client,
    event: dict,
    current_msg_id: int,
    embed: discord.Embed,
    view: discord.ui.View | None = None,
) -> None:
    """Edit whichever lineup message was NOT just interacted with."""
    race_msg_id = event.get("race_msg_id")
    tm_msg_id   = event.get("tm_msg_id")
    tm_ch_id    = event.get("tm_ch_id")
    race_ch_id  = event.get("channel_id")

    if current_msg_id == race_msg_id and tm_msg_id and tm_ch_id:
        ch     = bot.get_channel(int(tm_ch_id))
        msg_id = int(tm_msg_id)
    elif current_msg_id == tm_msg_id and race_msg_id and race_ch_id:
        ch     = bot.get_channel(int(race_ch_id))
        msg_id = int(race_msg_id)
    else:
        return

    if ch is None:
        return
    try:
        msg = await ch.fetch_message(msg_id)
        if view is not None:
            await msg.edit(embed=embed, view=view)
        else:
            await msg.edit(embed=embed)
    except (discord.NotFound, discord.Forbidden):
        pass


# ── views ─────────────────────────────────────────────────────────────────────

class SlotSelect(discord.ui.Select):
    """Dropdown for drivers to pick / switch their lineup slot."""

    def __init__(self, event_id: int, slots: list[dict], lineup: dict, disabled: bool = False):
        norm = _normalize_lineup(lineup)
        options = []
        for slot in slots:
            key      = _slot_key(slot["car_id"], slot["slot_num"])
            occupants = norm.get(key, [])
            if occupants:
                # Show count so drivers know the slot already has someone
                desc = f"👥 {len(occupants)} driver(s) signed up"
            else:
                desc = "🟢 Open"
            options.append(discord.SelectOption(
                label=slot["label"],
                value=key,
                description=desc,
            ))

        super().__init__(
            placeholder="🏎️  Select your slot…",
            options=options,
            custom_id=f"slot_select_{event_id}",
            disabled=disabled,
            min_values=1,
            max_values=1,
            row=0,
        )
        self.event_id = event_id
        self.slots    = slots

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        selected_key  = self.values[0]
        member        = interaction.user
        guild         = interaction.guild
        old_slot_info = None
        target_slot   = None

        async with _get_lineup_lock(self.event_id):
            event = db.get_event(self.event_id)
            if not event or event["confirmed"]:
                await interaction.followup.send("❌ This event is no longer active.", ephemeral=True)
                return

            lineup = _normalize_lineup(event["lineup"])

            # Remove member from their current slot (if any, and if different)
            for k, occupants in list(lineup.items()):
                if member.id in occupants:
                    if k == selected_key:
                        # Already in this slot — nothing to do
                        await interaction.followup.send(
                            "You're already signed up for that slot.", ephemeral=True
                        )
                        return
                    old_slot_info = next(
                        (s for s in event["slots"]
                         if _slot_key(s["car_id"], s["slot_num"]) == k),
                        None,
                    )
                    occupants.remove(member.id)
                    if not occupants:
                        lineup.pop(k)
                    else:
                        lineup[k] = occupants
                    break

            target_slot = next(
                (s for s in self.slots
                 if _slot_key(s["car_id"], s["slot_num"]) == selected_key),
                None,
            )
            if not target_slot:
                await interaction.followup.send("❌ Slot not found.", ephemeral=True)
                return

            # Add member to the selected slot (multiple drivers allowed)
            if selected_key not in lineup:
                lineup[selected_key] = []
            lineup[selected_key].append(member.id)
            db.update_lineup(self.event_id, lineup)

        # Discord API calls outside the lock
        if old_slot_info:
            old_role = discord.utils.get(
                guild.roles, name=f"{RACE_ROLE_PREFIX}{old_slot_info['car_name']}"
            )
            if old_role and old_role in member.roles:
                await member.remove_roles(old_role, reason="Changed slot")

        role_name = f"{RACE_ROLE_PREFIX}{target_slot['car_name']}"
        race_role = discord.utils.get(guild.roles, name=role_name)
        if race_role is None:
            race_role = await guild.create_role(name=role_name, reason="Race event role")
        await member.add_roles(race_role, reason=f"Registered for {target_slot['label']}")

        event    = db.get_event(self.event_id)
        embed    = _lineup_embed(event, guild)
        new_view = LineupView(self.event_id, event["slots"], event["lineup"])
        interaction.client.add_view(new_view)
        await interaction.message.edit(embed=embed, view=new_view)
        await _sync_other_message(
            interaction.client, event, interaction.message.id, embed, view=new_view
        )


class LineupView(discord.ui.View):
    """Slot selector + Withdraw + TM Confirm. Rebuilt from DB on restart."""

    def __init__(
        self,
        event_id: int,
        slots: list[dict],
        lineup: dict,
        confirmed: bool = False,
    ):
        super().__init__(timeout=None)
        self.event_id = event_id
        self.slots    = slots

        self.add_item(SlotSelect(event_id, slots, lineup, disabled=confirmed))

        withdraw_btn = discord.ui.Button(
            label="🚪 Withdraw",
            style=discord.ButtonStyle.secondary,
            custom_id=f"withdraw_{event_id}",
            disabled=confirmed,
            row=1,
        )
        withdraw_btn.callback = self._withdraw_callback
        self.add_item(withdraw_btn)

        confirm_btn = discord.ui.Button(
            label="✅ Confirm Lineup",
            style=discord.ButtonStyle.success,
            custom_id=f"confirm_{event_id}",
            disabled=confirmed,
            row=1,
        )
        confirm_btn.callback = self._confirm_callback
        self.add_item(confirm_btn)

    async def _withdraw_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        member        = interaction.user
        guild         = interaction.guild
        old_slot_info = None

        async with _get_lineup_lock(self.event_id):
            event = db.get_event(self.event_id)
            if not event or event["confirmed"]:
                await interaction.followup.send("❌ Lineup is already confirmed.", ephemeral=True)
                return

            lineup  = _normalize_lineup(event["lineup"])
            removed = False

            for k, occupants in list(lineup.items()):
                if member.id in occupants:
                    old_slot_info = next(
                        (s for s in event["slots"]
                         if _slot_key(s["car_id"], s["slot_num"]) == k),
                        None,
                    )
                    occupants.remove(member.id)
                    if not occupants:
                        lineup.pop(k)
                    else:
                        lineup[k] = occupants
                    removed = True
                    break

            if not removed:
                await interaction.followup.send("❌ You're not in the lineup.", ephemeral=True)
                return

            db.update_lineup(self.event_id, lineup)

        if old_slot_info:
            old_role = discord.utils.get(
                guild.roles, name=f"{RACE_ROLE_PREFIX}{old_slot_info['car_name']}"
            )
            if old_role and old_role in member.roles:
                await member.remove_roles(old_role, reason="Withdrew from lineup")

        event    = db.get_event(self.event_id)
        embed    = _lineup_embed(event, guild)
        new_view = LineupView(self.event_id, event["slots"], event["lineup"])
        interaction.client.add_view(new_view)
        await interaction.message.edit(embed=embed, view=new_view)
        await _sync_other_message(
            interaction.client, event, interaction.message.id, embed, view=new_view
        )

    async def _confirm_callback(self, interaction: discord.Interaction):
        guild    = interaction.guild
        tm_role  = _resolve_cfg_role(guild, CFG_ROLE_TM,  ROLE_TEAM_MANAGER)
        ceo_role = _resolve_cfg_role(guild, CFG_ROLE_CEO, ROLE_CEO)

        has_auth = (
            (tm_role  and tm_role  in interaction.user.roles) or
            (ceo_role and ceo_role in interaction.user.roles)
        )
        if not has_auth:
            await interaction.response.send_message(
                "❌ Only Team Manager or CEO can confirm the lineup.", ephemeral=True
            )
            return

        db.confirm_event(self.event_id)
        _lineup_locks.pop(self.event_id, None)
        event = db.get_event(self.event_id)

        for child in self.children:
            child.disabled = True

        embed = _lineup_embed(event, guild)
        await interaction.response.edit_message(embed=embed, view=self)

        other_view = LineupView(self.event_id, event["slots"], event["lineup"], confirmed=True)
        await _sync_other_message(
            interaction.client, event, interaction.message.id, embed, view=other_view
        )

        confirm_msg = await interaction.channel.send(
            f"📌 **Lineup confirmed by {interaction.user.display_name}!**"
        )
        try:
            await confirm_msg.pin()
        except discord.Forbidden:
            pass


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
        results = {
            "positions": self.positions.value,
            "notes":     self.notes.value or "",
        }
        db.set_results(self.event_id, results, datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat())

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
        self.bot.loop.create_task(self._restore_views())

    async def _restore_views(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            for event in db.get_active_events(guild.id):
                if event["confirmed"] or event.get("channel_id") is None:
                    continue
                view = LineupView(
                    event["id"], event["slots"], event["lineup"], confirmed=False
                )
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
        car1="First car (required)",
        car2="Second car",
        car3="Third car",
        car4="Fourth car",
        car5="Fifth car",
        car6="Sixth car",
    )
    @app_commands.checks.has_any_role(ROLE_CEO, ROLE_TEAM_MANAGER)
    async def event(
        self,
        interaction: discord.Interaction,
        name: str,
        date: str,
        time: str,
        car1: str,
        car2: str | None = None,
        car3: str | None = None,
        car4: str | None = None,
        car5: str | None = None,
        car6: str | None = None,
    ):
        guild = interaction.guild

        try:
            dt = datetime.datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid date or time. Use YYYY-MM-DD for date and HH:MM for time.",
                ephemeral=True,
            )
            return

        car_names = [c.strip() for c in [car1, car2, car3, car4, car5, car6] if c]
        await interaction.response.defer(ephemeral=True)

        try:
            slots = _build_slots(guild.id, car_names)
        except ValueError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        date_str = dt.strftime("%Y-%m-%dT%H:%M")
        event_id = db.create_event(guild.id, name, date_str, slots)

        # Create race channel — visible to Driver, Driver-Notification, CEO, TM; hidden from @everyone
        category       = await _get_or_create_races_category(guild)
        safe_name      = name.lower().replace(" ", "-")[:80]
        driver_role    = _resolve_cfg_role(guild, CFG_ROLE_DRIVER, ROLE_DRIVER)
        notif_role     = discord.utils.get(guild.roles, name=ROLE_DRIVER_NOTIF)
        ceo_role_ch    = _resolve_cfg_role(guild, CFG_ROLE_CEO, ROLE_CEO)
        tm_role_ch     = _resolve_cfg_role(guild, CFG_ROLE_TM,  ROLE_TEAM_MANAGER)
        ch_overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me:           discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        for role in (driver_role, notif_role, ceo_role_ch, tm_role_ch):
            if role:
                ch_overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        race_channel = await guild.create_text_channel(
            name=f"race-{safe_name}",
            category=category,
            overwrites=ch_overwrites,
            reason=f"Race event: {name}",
        )
        db.set_event_channel(event_id, race_channel.id)

        # Create one discussion thread per unique car under the race channel
        unique_car_names = list(dict.fromkeys(s["car_name"] for s in slots))
        for car_name in unique_car_names:
            try:
                await race_channel.create_thread(
                    name=f"{car_name} — setup & strategy",
                    type=discord.ChannelType.public_thread,
                    auto_archive_duration=10080,  # 7 days
                    reason=f"Car thread: {car_name}",
                )
            except discord.Forbidden:
                pass

        event = db.get_event(event_id)
        view  = LineupView(event_id, slots, event["lineup"])
        self.bot.add_view(view)

        embed = _lineup_embed(event, guild)
        embed.description = (
            f"📅 **{date_str} UTC**\n\n"
            "Use the dropdown to register for a slot. "
            "You can change your pick until the lineup is confirmed."
        )

        notif_role = discord.utils.get(guild.roles, name=ROLE_DRIVER_NOTIF)
        ping_text  = notif_role.mention if notif_role else ""

        race_msg = await race_channel.send(
            f"🏁 **Race Event: {name}**\n"
            f"Organised by {interaction.user.mention} {ping_text}",
            embed=embed,
            view=view,
        )

        tm_ch  = await _get_or_create_lineup_channel(guild)
        tm_msg = None
        if tm_ch:
            tm_view = LineupView(event_id, slots, event["lineup"])
            tm_msg  = await tm_ch.send(
                f"📋 **Lineup draft — {name}** ({date_str} UTC)\n"
                f"Race channel: {race_channel.mention}",
                embed=embed,
                view=tm_view,
            )

        db.set_event_messages(
            event_id,
            race_msg_id=race_msg.id,
            tm_ch_id=tm_ch.id   if tm_ch  and tm_msg else 0,
            tm_msg_id=tm_msg.id if tm_msg else 0,
        )

        await interaction.followup.send(
            f"✅ Race event **{name}** created: {race_channel.mention}", ephemeral=True
        )

    @event.autocomplete("car1")
    @event.autocomplete("car2")
    @event.autocomplete("car3")
    @event.autocomplete("car4")
    @event.autocomplete("car5")
    @event.autocomplete("car6")
    async def car_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        matches = db.search_cars(interaction.guild_id, current, limit=10)
        return [app_commands.Choice(name=m["name"], value=m["name"]) for m in matches]

    # ── /lineup-set ───────────────────────────────────────────────────────────

    @app_commands.command(
        name="lineup-set",
        description="Place a driver in a specific slot (TM / CEO only).",
    )
    @app_commands.describe(
        driver="The driver to place",
        slot="Slot to assign — pick from the list (shows current occupant)",
    )
    @app_commands.checks.has_any_role(ROLE_CEO, ROLE_TEAM_MANAGER)
    async def lineup_set(
        self, interaction: discord.Interaction, driver: discord.Member, slot: str
    ):
        guild = interaction.guild
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
                f"❌ Slot not found. Available: {slot_names}", ephemeral=True
            )
            return

        async with _get_lineup_lock(event["id"]):
            event   = db.get_event(event["id"])
            lineup  = _normalize_lineup(event["lineup"])
            new_key = _slot_key(target_slot["car_id"], target_slot["slot_num"])

            # Remove driver from old slot (if any)
            for k, occupants in list(lineup.items()):
                if driver.id in occupants:
                    occupants.remove(driver.id)
                    if not occupants:
                        lineup.pop(k)
                    else:
                        lineup[k] = occupants
                    break

            # Add driver to new slot
            if new_key not in lineup:
                lineup[new_key] = []
            if driver.id not in lineup[new_key]:
                lineup[new_key].append(driver.id)
            db.update_lineup(event["id"], lineup)

        await interaction.response.send_message(
            f"✅ {driver.mention} placed in **{target_slot['label']}**.", ephemeral=True
        )

    @lineup_set.autocomplete("slot")
    async def lineup_set_slot_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        event = self._event_for_channel(interaction.guild_id, interaction.channel_id)
        if event is None:
            return []

        lineup  = _normalize_lineup(event["lineup"])
        choices = []
        for slot in event["slots"]:
            if current and current.lower() not in slot["label"].lower():
                continue
            key       = _slot_key(slot["car_id"], slot["slot_num"])
            occupants = lineup.get(key, [])
            if occupants:
                names = []
                for mid in occupants:
                    m = interaction.guild.get_member(mid)
                    names.append(m.display_name if m else str(mid))
                desc = f"Drivers: {', '.join(names)}"
            else:
                desc = "Empty"
            choices.append(app_commands.Choice(
                name=f"{slot['label']} — {desc}",
                value=slot["label"],
            ))
        return choices[:25]

    # ── /lineup-remove ────────────────────────────────────────────────────────

    @app_commands.command(
        name="lineup-remove",
        description="Remove a driver from the lineup (TM / CEO only).",
    )
    @app_commands.describe(driver="The driver to remove")
    @app_commands.checks.has_any_role(ROLE_CEO, ROLE_TEAM_MANAGER)
    async def lineup_remove(self, interaction: discord.Interaction, driver: discord.Member):
        guild = interaction.guild
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

        old_slot_info = None

        async with _get_lineup_lock(event["id"]):
            event  = db.get_event(event["id"])
            lineup = _normalize_lineup(event["lineup"])

            for k, occupants in list(lineup.items()):
                if driver.id in occupants:
                    old_slot_info = next(
                        (s for s in event["slots"]
                         if _slot_key(s["car_id"], s["slot_num"]) == k),
                        None,
                    )
                    occupants.remove(driver.id)
                    if not occupants:
                        lineup.pop(k)
                    else:
                        lineup[k] = occupants
                    break

            if old_slot_info is None:
                await interaction.response.send_message(
                    f"❌ {driver.mention} is not in the lineup.", ephemeral=True
                )
                return

            db.update_lineup(event["id"], lineup)

        if old_slot_info:
            old_role = discord.utils.get(
                guild.roles, name=f"{RACE_ROLE_PREFIX}{old_slot_info['car_name']}"
            )
            if old_role and old_role in driver.roles:
                await driver.remove_roles(old_role, reason="Removed from lineup by TM")

        await interaction.response.send_message(
            f"✅ {driver.mention} removed from **{old_slot_info['label']}**.", ephemeral=True
        )

    # ── /event-cancel ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="event-cancel",
        description="Cancel this race event, remove Race-* roles, and optionally delete the channel (TM / CEO).",
    )
    @app_commands.describe(delete_channel="Delete the race channel after cancelling? (default: yes)")
    @app_commands.checks.has_any_role(ROLE_CEO, ROLE_TEAM_MANAGER)
    async def event_cancel(
        self,
        interaction: discord.Interaction,
        delete_channel: bool = True,
    ):
        guild = interaction.guild
        event = self._event_for_channel(guild.id, interaction.channel_id)
        if event is None:
            await interaction.response.send_message(
                "❌ No active event found for this channel.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Clean up Race-* roles for all registered drivers
        await _cleanup_race_roles(guild, event["id"])

        db.cancel_event(event["id"])
        _lineup_locks.pop(event["id"], None)

        await interaction.followup.send(
            f"✅ **{event['name']}** has been cancelled and Race-* roles removed.",
            ephemeral=True,
        )

        if delete_channel:
            ch = guild.get_channel(event["channel_id"])
            if ch:
                await ch.delete(reason=f"Event cancelled by {interaction.user.display_name}")

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
        """Return the active event whose race channel OR TM lineup channel matches.

        Also handles commands run inside a thread under the race channel by
        checking the thread's parent channel ID.
        """
        # Resolve parent_id in case we're inside a thread
        channel = self.bot.get_channel(channel_id)
        parent_id: int | None = None
        if isinstance(channel, discord.Thread):
            parent_id = channel.parent_id

        active = db.get_active_events(guild_id)

        for ev in active:
            race_ch_id = ev.get("channel_id")
            if race_ch_id and (race_ch_id == channel_id or race_ch_id == parent_id):
                return ev
            tm_ch_id = ev.get("tm_ch_id")
            if tm_ch_id and (int(tm_ch_id) == channel_id or int(tm_ch_id) == parent_id):
                return ev

        # Fallback: if only one active event in this guild, return it
        if len(active) == 1:
            return active[0]

        return None

    # ── background: reminders + restriction + cleanup ─────────────────────────

    @tasks.loop(minutes=5)
    async def reminder_task(self):
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

        for guild in self.bot.guilds:
            for event in db.get_active_events(guild.id):
                if event.get("channel_id") is None:
                    continue
                ch = self.bot.get_channel(event["channel_id"])
                if ch is None:
                    continue

                try:
                    race_dt = datetime.datetime.fromisoformat(event["date_utc"])
                except ValueError:
                    continue

                delta = race_dt - now

                # T-24h reminder
                if (
                    not event["reminder_24h_sent"]
                    and datetime.timedelta(hours=23, minutes=45) <= delta <= datetime.timedelta(hours=24, minutes=15)
                ):
                    mentions = self._driver_mentions(guild, event)
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
                    embed    = _lineup_embed(event, guild)
                    mentions = self._driver_mentions(guild, event)
                    await ch.send(
                        f"🚨 **1-hour reminder!** The race **{event['name']}** starts soon!\n"
                        f"{mentions}",
                        embed=embed,
                    )
                    db.mark_reminder(event["id"], "1h")

        # At race start: restrict channel to confirmed drivers only
        for event in db.get_events_due_restriction(now.isoformat()):
            guild = self.bot.get_guild(event["guild_id"])
            if guild is None:
                continue
            ch = self.bot.get_channel(event["channel_id"])
            if ch is None:
                db.mark_restricted(event["id"])
                continue
            await self._restrict_to_drivers(guild, ch, event)
            db.mark_restricted(event["id"])

        # 48h post-race: clean up Race-* roles
        cleanup_cutoff = (now - datetime.timedelta(hours=48)).isoformat()
        for event in db.get_events_due_cleanup(cleanup_cutoff):
            guild = self.bot.get_guild(event["guild_id"])
            if guild:
                await _cleanup_race_roles(guild, event["id"])
                db.mark_roles_cleaned(event["id"])

    async def _restrict_to_drivers(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        event: dict,
    ) -> None:
        """Send a warning, then lock the race channel to confirmed lineup drivers + CEO/TM."""
        await channel.send(
            "🔒 **Race has started.** This channel is now restricted to confirmed drivers."
        )
        lineup   = _normalize_lineup(event["lineup"])
        ceo_role = _resolve_cfg_role(guild, CFG_ROLE_CEO, ROLE_CEO)
        tm_role  = _resolve_cfg_role(guild, CFG_ROLE_TM,  ROLE_TEAM_MANAGER)

        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me:           discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        if ceo_role:
            overwrites[ceo_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        if tm_role:
            overwrites[tm_role]  = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        all_member_ids: set[int] = set()
        for occupants in lineup.values():
            all_member_ids.update(occupants)

        for member_id in all_member_ids:
            member = guild.get_member(member_id)
            if member:
                overwrites[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        try:
            await channel.edit(overwrites=overwrites, reason="Race started — restricted to drivers")
        except discord.Forbidden:
            pass

    def _driver_mentions(self, guild: discord.Guild, event: dict) -> str:
        """Return @mentions of Race-<Car> roles for this event (one ping per car name)."""
        car_names = list(dict.fromkeys(s["car_name"] for s in event["slots"]))
        mentions  = []
        for car_name in car_names:
            role = discord.utils.get(guild.roles, name=f"{RACE_ROLE_PREFIX}{car_name}")
            if role:
                mentions.append(role.mention)
        return " ".join(mentions) if mentions else ""

    @reminder_task.before_loop
    async def before_reminder(self):
        await self.bot.wait_until_ready()

    # ── error handlers ────────────────────────────────────────────────────────

    @event.error
    @lineup_set.error
    @lineup_remove.error
    @event_cancel.error
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
