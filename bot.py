import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from typing import Optional, Dict, List
from flask import Flask
import os
import asyncio

app = Flask(__name__)
port = int(os.environ.get("PORT", 4000))

@app.route("/")
def hello_world():
    return "Hello World!"

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


def parse_quick_stats_from_soup(soup: BeautifulSoup) -> Optional[Dict]:
    """Try to find and parse a Quick Stats table from the team page soup.
    Returns a dict with keys: 'categories': List[str], 'rows': Dict[label, List[str]]
    or None if no suitable table found.
    """
    from bs4 import Tag

    # Helper to parse classic <table> elements
    def parse_table(t):
        headers = [th.get_text(strip=True) for th in t.find_all("th")]
        if headers:
            if len(headers) > 1 and (headers[0].strip() == "" or any(x.lower() in headers[0].lower() for x in ("best", "rank", "label"))):
                categories = headers[1:]
            else:
                categories = headers
        else:
            first_tr = t.find("tr")
            if first_tr:
                cells = first_tr.find_all(["td", "th"]) or []
                categories = [c.get_text(strip=True) for c in cells[1:]] if len(cells) > 1 else []
            else:
                categories = []

        rows_local: Dict[str, List[str]] = {}
        for tr in t.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            texts = [c.get_text(" ", strip=True) for c in cells]
            if len(texts) == 1:
                continue
            label = texts[0]
            vals = texts[1:]
            rows_local[label] = vals

        return {"categories": categories, "rows": rows_local}

    # Helper to parse div-based grid tables (the site uses a grid of divs in some themes)
    def parse_div_grid(container):
        # collect direct child divs (preserve order)
        children = [c for c in container.find_all(recursive=False) if isinstance(c, Tag) and c.name == "div"]
        if not children:
            # fallback to any descendant divs
            children = [c for c in container.find_all("div")]

        # find headers
        headers = []
        for c in children:
            classes = c.get("class") or []
            if any("header" == cls or "header" in cls for cls in classes):
                text = c.get_text(" ", strip=True)
                headers.append(text)

        # If no header elements, try to find the first sequence of divs that look like headers
        if not headers:
            # try the first row-like group: look for multiple children with no 'val' class
            guess = []
            for c in children[:10]:
                t = c.get_text(" ", strip=True)
                guess.append(t)
            if guess:
                headers = guess

        # categories usually start after an initial empty row-label header
        categories = headers[1:] if len(headers) > 1 else headers

        rows_local: Dict[str, List[str]] = {}

        i = 0
        n = len(children)
        while i < n:
            c = children[i]
            classes = c.get("class") or []
            # row label
            if any("row-label" == cls or "row-label" in cls for cls in classes):
                label = c.get_text(" ", strip=True)
                vals = []
                j = i + 1
                # collect following 'val' elements until next 'row-label' or end
                while j < n:
                    cj = children[j]
                    classes_j = cj.get("class") or []
                    if any("row-label" == cls or "row-label" in cls for cls in classes_j):
                        break
                    if any("val" == cls or "val" in cls for cls in classes_j):
                        vals.append(cj.get_text(" ", strip=True))
                    j += 1
                rows_local[label] = vals
                i = j
            else:
                i += 1

        if not rows_local and headers:
            # fallback: try to chunk children into rows based on header length
            # assume first header is a placeholder, header count -> cols
            col_count = max(1, len(categories))
            # scan for any text nodes that might be row labels
            text_children = [c.get_text(" ", strip=True) for c in children if c.get_text(strip=True) != ""]
            if text_children:
                # chunk after the first header texts
                # skip header_count items then group
                try:
                    start = len(headers)
                    tail = text_children[start:]
                    for idx in range(0, len(tail), col_count + 1):
                        label = tail[idx]
                        vals = tail[idx+1: idx+1+col_count]
                        rows_local[label] = vals
                except Exception:
                    pass

        if not categories and rows_local:
            maxlen = max((len(v) for v in rows_local.values()), default=0)
            categories = [f"Col {i+1}" for i in range(maxlen)]

        if not rows_local:
            return None

        return {"categories": categories, "rows": rows_local}

    # 1) Try to find a heading containing "Quick Stats" and prefer a following div-based grid
    heading = soup.find(lambda tag: tag.name in ("h1", "h2", "h3", "h4", "h5", "div", "span") and tag.get_text(strip=True) and "quick stats" in tag.get_text(strip=True).lower())
    if heading:
        # try div-grid first
        div_grid = heading.find_next(lambda tag: tag.name == "div" and tag.get("class") and any("table" in cls or "table" == cls for cls in tag.get("class")))
        if div_grid:
            parsed = parse_div_grid(div_grid)
            if parsed:
                keys_joined = " ".join(k.lower() for k in parsed.get("rows", {}).keys())
                if any(k in keys_joined for k in ("best", "opr", "rank", "percentile")):
                    return parsed
                # return parsed anyway as it is likely the Quick Stats area
                return parsed

        # fallback: try a following <table>
        t = heading.find_next("table")
        if t:
            parsed = parse_table(t)
            keys_joined = " ".join(k.lower() for k in parsed.get("rows", {}).keys())
            if any(k in keys_joined for k in ("best", "opr", "rank", "percentile")):
                return parsed

    # 2) Scan for any div-based grids site-wide and pick best candidate
    div_candidates = []
    for div in soup.find_all("div"):
        classes = div.get("class") or []
        if any("table" in cls or "table" == cls for cls in classes):
            parsed = parse_div_grid(div)
            if parsed:
                div_candidates.append(parsed)

    # prefer a div candidate that has keyword rows
    for parsed in div_candidates:
        keys_joined = " ".join(k.lower() for k in parsed.get("rows", {}).keys())
        if any(k in keys_joined for k in ("best", "opr", "rank", "percentile")):
            return parsed
    if div_candidates:
        return div_candidates[0]

    # 3) Classic <table> scanning (original logic)
    table_candidates = []
    # Tables whose headers mention Total NP / Best OPR / Rank
    for t in soup.find_all("table"):
        th_texts = [th.get_text(strip=True) for th in t.find_all("th")]
        joined = " ".join(th_texts).lower()
        if any(k in joined for k in ("total np", "best opr", "rank / percentile", "total np")):
            if t not in table_candidates:
                table_candidates.append(t)
    # add any remaining tables
    for t in soup.find_all("table"):
        if t not in table_candidates:
            table_candidates.append(t)

    parsed_result = None
    for t in table_candidates:
        parsed = parse_table(t)
        keys_joined = " ".join(k.lower() for k in parsed.get("rows", {}).keys())
        if any(k in keys_joined for k in ("best", "opr", "rank", "percentile")):
            parsed_result = parsed
            break

    if not parsed_result and table_candidates:
        parsed_result = parse_table(table_candidates[0])

    return parsed_result

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
                parsed = parse_quick_stats_from_soup(soup)
                if parsed:
                    categories: List[str] = parsed.get("categories", [])
                    rows: Dict[str, List[str]] = parsed.get("rows", {})

                    # Build an embed for nicer formatting
                    embed = discord.Embed(title=f"Team {team_number} — Quick Stats")
                    embed.description = f"{name} — {city} {state} {country}".strip()

                    # Build fields for the specific rows we care about: Best OPR and Rank / Percentile
                    def find_row(keys):
                        for k in rows.keys():
                            kl = k.lower()
                            if any(x in kl for x in keys):
                                return k
                        return None

                    best_label = find_row(("best", "opr"))
                    rank_label = find_row(("rank", "percentile"))

                    # Prefer to display per-category columns (Total NP, Auto, Teleop, Endgame)
                    added_any = False
                    if categories:
                        for i, cat in enumerate(categories):
                            best_val = ""
                            rank_val = ""
                            if best_label:
                                vals = rows.get(best_label, [])
                                if i < len(vals):
                                    best_val = vals[i]
                            if rank_label:
                                vals = rows.get(rank_label, [])
                                if i < len(vals):
                                    rank_val = vals[i]

                            field_lines = []
                            if best_label and best_val:
                                field_lines.append(f"Best OPR: {best_val}")
                            if rank_label and rank_val:
                                field_lines.append(f"Rank / Percentile: {rank_val}")

                            if field_lines:
                                embed.add_field(name=cat, value="\n".join(field_lines), inline=True)
                                added_any = True

                    # Fallback: if nothing added, show rows as separate fields
                    if not added_any:
                        for label, vals in rows.items():
                            parts = []
                            for i, cat in enumerate(categories):
                                v = vals[i] if i < len(vals) else ""
                                parts.append(f"{cat}: {v}")
                            embed.add_field(name=label, value=" | ".join(parts), inline=False)

                    await ctx.send(content=None, embed=embed)
                    return
            except Exception:
                # parsing error — ignore and continue to fallback
                pass
        else:
            msg_lines.append(f"(Could not fetch team page: HTTP {html_resp.status_code})")
    except requests.RequestException:
        msg_lines.append("(Failed to fetch team page)")

    # Fallback: send basic text message if embed not produced
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

import threading

def run_flask():
    app.run(host="0.0.0.0", port=port, use_reloader=False)

async def start_bot():
    await bot.start(TOKEN)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()

    # prevent rapid reconnect spam
    while True:
        try:
            asyncio.run(start_bot())
        except discord.HTTPException as e:
            print("Login rate-limited. Waiting 60s before retrying...")
            import time
            time.sleep(60)
        except Exception as e:
            raise e