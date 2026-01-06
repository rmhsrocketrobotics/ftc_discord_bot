import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise SystemExit("DISCORD_TOKEN not set. See README.md and .env.example")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

@bot.command(name="ping")
async def ping(ctx):
    """Responds with Pong!"""
    await ctx.send("Pong!")

@bot.command(name="echo")
async def echo(ctx, *, message: str):
    """Echo back a message provided by the user."""
    await ctx.send(message)

@bot.event
async def on_message(message):
    # ignore our own messages
    if message.author == bot.user:
        return

    if message.content.lower() == "hello":
        await message.channel.send(f"Hello, {message.author.mention}!")

    # allow commands to be processed
    await bot.process_commands(message)

if __name__ == "__main__":
    bot.run(TOKEN)
