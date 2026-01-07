import os
import threading
import discord
from discord.ext import commands
from discord.ext.commands import cooldown, BucketType, CommandOnCooldown
from dotenv import load_dotenv
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from typing import Optional, Dict, List
from flask import Flask

# ------------------ Flask (Render keep-alive) ------------------

app = Flask(__name__)
port = int(os.environ.get("PORT", 4000))

@app.route("/")
def hello_world():
    return "Hello World!"

# ------------------ Discord Setup ------------------

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise SystemExit("DISCORD_TOKEN not set.")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ FTC Scout API ------------------

API_BASE = "https://api.ftcscout.org/rest/v1"

def api_get(path: str, params: dict | None = None):
    url = API_BASE + path
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json(), None
        elif resp.status_code == 404:
            return None, "Not found (404)"
        else:
            return None, f"API error {resp.status_code}"
    except requests.RequestException as e:
        return None, str(e)

# ------------------ HTML Parsing ------------------

def parse_quick_stats_from_soup(soup: BeautifulSoup) -> Optional[Dict]:
    table = soup.find("table")
    if not table:
        return None

    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    categories = headers[1:] if len(headers) > 1 else []

    rows = {}
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        label = cells[0].get_text(strip=True)
        vals = [c.get_text(strip=True) for c in cells[1:]]
        rows[label] = vals

    return {"categories": categories, "rows": rows} if rows else None

# ------------------ Events ------------------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, CommandOnCooldown):
        await ctx.send(f"⏳ Slow down! Try again in {error.retry_after:.1f}s")
    else:
        raise error

# ------------------ Commands ------------------

@bot.command(name="events")
async def events(ctx, *, query: str = None):
    params = {"limit": 8}
    if query:
        params["searchText"] = query

    data, err = api_get("/events/search", params)
    if err or not data:
        await ctx.send("No events found.")
        return

    lines = [
        f"{ev.get('season')} {ev.get('code')} — {ev.get('name')}"
        for ev in data
    ]
    await ctx.send("\n".join(lines)[:1900])

@bot.command(name="team")
@cooldown(1, 5, BucketType.user)  # 🔒 RATE LIMIT FIX
async def team(ctx, team_number: int):
    data, err = api_get(f"/teams/{team_number}")
    if err:
        await ctx.send(f"Error: {err}")
        return

    name = data.get("name", "")
    city = data.get("city", "")
    state = data.get("state", "")
    country = data.get("country", "")

    embed = discord.Embed(
        title=f"Team {team_number}",
        description=f"{name}\n{city} {state} {country}"
    )

    html_url = f"https://ftcscout.org/teams/{team_number}"
    try:
        html = requests.get(html_url, timeout=10).text
        soup = BeautifulSoup(html, "html.parser")
        parsed = parse_quick_stats_from_soup(soup)

        if parsed:
            categories = parsed["categories"]
            rows = parsed["rows"]

            count = 0
            for label, vals in rows.items():
                if count >= 20:  # 🧯 embed field safety cap
                    break
                value = " | ".join(
                    f"{categories[i]}: {vals[i]}"
                    for i in range(min(len(vals), len(categories)))
                )
                embed.add_field(name=label, value=value, inline=False)
                count += 1
    except Exception:
        pass

    await ctx.send(embed=embed)

@bot.command(name="commands")
async def commands_list(ctx):
    lines = [
        f"!{c.name} {c.signature} — {c.help or ''}".strip()
        for c in bot.commands
        if not c.hidden
    ]
    await ctx.send("\n".join(lines)[:1900])

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.content.lower() == "hello":
        await message.channel.send(f"Hello, {message.author.mention}!")

    await bot.process_commands(message)

# ------------------ Run ------------------

def run_flask():
    app.run(host="0.0.0.0", port=port, use_reloader=False)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(TOKEN)
