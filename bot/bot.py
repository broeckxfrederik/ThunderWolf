"""
ThunderWolf Discord Bot — entry point
"""

import asyncio
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN    = os.environ["DISCORD_TOKEN"]
GUILD_ID = int(os.environ["GUILD_ID"])

COGS = [
    "cogs.setup",
    "cogs.greeting",
    "cogs.reaction_roles",
    "cogs.race_event",
]


class ThunderWolf(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members   = True  # privileged — enable in Developer Portal → Bot → Privileged Gateway Intents
        intents.reactions = True  # needed for reaction roles

        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        for cog in COGS:
            await self.load_extension(cog)
            print(f"  ✓ loaded {cog}")

        # Sync slash commands to the guild (instant) and globally (up to 1 h)
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        print("  ✓ slash commands synced")

    async def on_ready(self):
        print(f"\n🐺 ThunderWolf is online as {self.user} (id={self.user.id})")
        await self.change_presence(activity=discord.Game(name="Managing the team 🏁"))


async def main():
    async with ThunderWolf() as bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
