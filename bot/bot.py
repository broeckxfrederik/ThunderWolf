"""
ThunderWolf Discord Bot — entry point
"""

import asyncio
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

import db

load_dotenv()

TOKEN    = os.environ["DISCORD_TOKEN"]
GUILD_ID = int(os.environ["GUILD_ID"])

COGS = [
    "cogs.setup",
    "cogs.cars",
    "cogs.greeting",
    "cogs.reaction_roles",
    "cogs.race_event",
    "cogs.roles",
]


class ThunderWolf(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members         = True   # needed for on_member_join
        intents.message_content = True   # needed for reading messages
        intents.reactions       = True   # needed for reaction roles
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Initialise the database first — everything else depends on it
        db.init_db()
        print("  ✓ database ready")

        for cog in COGS:
            await self.load_extension(cog)
            print(f"  ✓ loaded {cog}")

        # Sync slash commands to the guild (instant) and globally (up to 1 h)
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        try:
            await self.tree.sync(guild=guild)
            print("  ✓ slash commands synced")
        except discord.Forbidden:
            print(
                "  ⚠ slash command sync failed (403 Missing Access).\n"
                "    Re-invite the bot using an OAuth2 URL that includes BOTH\n"
                "    the 'bot' AND 'applications.commands' scopes, then restart."
            )

    async def on_ready(self):
        print(f"\n🐺 ThunderWolf is online as {self.user} (id={self.user.id})")
        await self.change_presence(activity=discord.Game(name="Managing the team 🏁"))


async def main():
    async with ThunderWolf() as bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
