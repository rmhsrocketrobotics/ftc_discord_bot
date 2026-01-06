import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import requests
from datetime import datetime
from bs4 import BeautifulSoup

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise SystemExit("DISCORD_TOKEN not set. See README.md and .env.example")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Simple FTC Scout API client
API_BASE = "https://api.ftcscout.org/rest/v1"


def api_get(path: str, params: dict | None = None):
    """GET helper for the FTC Scout REST API. Returns (json, error_message).
    On success returns (data, None). On failure returns (None, message).
    """
    url = API_BASE + path
    try:
        resp = requests.get(url, params=params, timeout=10)
    except requests.RequestException as e:
        return None, f"Request failed: {e}"

    if resp.status_code == 200:
        try:
            return resp.json(), None
        except ValueError:
            return None, "Invalid JSON response"
    elif resp.status_code == 404:
        return None, "Not found (404)"
    else:
        return None, f"API error {resp.status_code}: {resp.text}"

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

@bot.command(name="events")
async def events(ctx, *, query: str = None):
    """Search events. Usage: `!events <search text>` (returns up to 8 results)."""
    params = {"limit": 8}
    if query:
        params["searchText"] = query

    data, err = api_get("/events/search", params=params)
    if err:
        await ctx.send(f"Error: {err}")
        return

    if not data:
        await ctx.send("No events found.")
        return

    lines = []
    for ev in data:
        season = ev.get("season")
        code = ev.get("code")
        name = ev.get("name")
        start = ev.get("startDate") or "?"
        lines.append(f"{season} {code} — {name} ({start})")

    await ctx.send("\n".join(lines)[:1900])


@bot.command(name="event")
async def event(ctx, code: str, season: int | None = None):
    """Get event details. Usage: `!event <code> [season]` (season defaults to current year)."""
    if season is None:
        season = datetime.now().year

    path = f"/events/{season}/{code}"
    data, err = api_get(path)
    if err:
        await ctx.send(f"Error: {err}")
        return

    # Format basic event information
    name = data.get("name")
    start = data.get("startDate")
    end = data.get("endDate")
    city = data.get("city")
    state = data.get("state")
    venue = data.get("venueName") or ""
    msg = f"{season} {code} — {name}\n{start} to {end}\n{venue} {city or ''} {state or ''}".strip()
    await ctx.send(msg[:1900])


@bot.command(name="teams")
async def teams(ctx, event_code: str, season: int | None = None):
    """List teams at an event. Usage: `!teams <event_code> [season]`"""
    if season is None:
        season = datetime.now().year

    path = f"/events/{season}/{event_code}/teams"
    data, err = api_get(path)
    if err:
        await ctx.send(f"Error: {err}")
        return

    if not data:
        await ctx.send("No teams found for that event.")
        return

    lines = []
    for te in data[:40]:
        num = te.get("teamNumber") or te.get("teamNumber")
        nick = te.get("teamNickname") or te.get("nickname") or ""
        place = te.get("city") or te.get("state") or ""
        lines.append(f"#{num} — {nick} {place}")

    await ctx.send("\n".join(lines)[:1900])


@bot.command(name="team")
async def team(ctx, team_number: int):
    """Get basic team info. Usage: `!team <team_number>`"""
    path = f"/teams/{team_number}"
    data, err = api_get(path)
    if err:
        await ctx.send(f"Error: {err}")
        return

    name = data.get("name") or data.get("teamNickname") or ""
    city = data.get("city") or ""
    state = data.get("state") or ""
    country = data.get("country") or ""
    msg_lines = [f"#{team_number} — {name}", f"{city} {state} {country}".strip()]

    # Try to fetch the public HTML team page and parse the Quick Stats table
    html_url = f"https://ftcscout.org/teams/{team_number}"
    try:
        html_resp = requests.get(html_url, timeout=10)
        if html_resp.status_code == 200:
            try:
                soup = BeautifulSoup(html_resp.text, "html.parser")

                # Find a heading that contains "Quick Stats" then the following table
                quick_heading = soup.find(lambda tag: tag.name in ("h1", "h2", "h3", "h4", "h5", "div", "span") and "Quick Stats" in tag.get_text())
                table = None
                if quick_heading:
                    table = quick_heading.find_next("table")
                if not table:
                    # fallback: find any table that has header cells containing "Best OPR" or "Rank" text
                    for t in soup.find_all("table"):
                        th_texts = [th.get_text(strip=True) for th in t.find_all("th")]
                        if any("Best OPR" in t for t in th_texts) or any("Rank" in t for t in th_texts) or any("Total NP" in t for t in th_texts):
                            table = t
                            break

                if table:
                    # parse headers (categories)
                    headers = [th.get_text(strip=True) for th in table.find_all("th")]
                    # parse rows
                    rows = []
                    for tr in table.find_all("tr"):
                        cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"]) ]
                        if cols:
                            rows.append(cols)

                    # Build a readable quick-stats block if rows look reasonable
                    if headers and rows:
                        # find index rows for "Best OPR" and "Rank / Percentile" (match approximate)
                        stat_lines = []
                        # Attempt to map columns by header positions (skip first header if it's a row label)
                        # If the first header is blank or looks like a label, treat subsequent headers as categories
                        if len(headers) > 1 and (headers[0].lower().startswith("best") or headers[0].strip() == ""):
                            categories = headers[1:]
                        else:
                            categories = headers

                        # find meaningful rows by label
                        for r in rows:
                            label = r[0].strip() if r else ""
                            if any(k in label for k in ("Best OPR", "Best OPR".lower(), "Best")):
                                # values are remaining columns
                                vals = r[1:] if len(r) > 1 else []
                                items = []
                                for cat, val in zip(categories, vals):
                                    items.append(f"{cat}: {val}")
                                stat_lines.append(" | ".join(items))
                            if any(k in label for k in ("Rank", "Percentile", "Rank / Percentile")) or "percentile" in label.lower():
                                vals = r[1:] if len(r) > 1 else []
                                items = []
                                for cat, val in zip(categories, vals):
                                    items.append(f"{cat}: {val}")
                                stat_lines.append(" | ".join(items))

                        if stat_lines:
                            msg_lines.append("Quick Stats:")
                            msg_lines.extend(stat_lines)
            except Exception:
                # parsing error — ignore and continue
                pass
        else:
            msg_lines.append(f"(Could not fetch team page: HTTP {html_resp.status_code})")
    except requests.RequestException:
        msg_lines.append("(Failed to fetch team page)")

    await ctx.send("\n".join(msg_lines)[:1900])


@bot.command(name="commands")
async def commands_list(ctx):
    """List all available bot commands with usage and short descriptions."""
    # Determine prefix (commands.Bot can accept a str or callable; we assume the common string prefix)
    prefix = "!"
    try:
        if isinstance(bot.command_prefix, str):
            prefix = bot.command_prefix
        elif isinstance(bot.command_prefix, (list, tuple)) and bot.command_prefix:
            prefix = bot.command_prefix[0]
    except Exception:
        prefix = "!"

    lines = []
    for c in sorted(bot.commands, key=lambda x: x.name):
        if getattr(c, "hidden", False):
            continue
        sig = str(c.signature) if getattr(c, "signature", None) is not None else ""
        usage = f"{prefix}{c.name} {sig}".strip()
        desc = c.help or "No description available."
        # Collapse newlines in description
        desc = " ".join(line.strip() for line in desc.splitlines())
        lines.append(f"{usage} — {desc}")

    if not lines:
        await ctx.send("No commands available.")
        return

    # Send in one message (truncate safely)
    await ctx.send("\n".join(lines)[:1900])

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
