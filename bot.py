import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import requests
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime
from bs4 import BeautifulSoup, Tag
from typing import Optional, Dict, List
from flask import Flask
import asyncio
import threading

# Flask health endpoint (kept for Render/hosting readiness)
app = Flask(__name__)
port = int(os.environ.get("PORT", 4000))

@app.route("/")
def hello_world():
    return "Hello World!"

# Load secrets from .env when running locally
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
TOA_KEY = os.getenv("TOA_KEY") 
TOA_API_BASE = "https://theorangealliance.org/api"

if not TOKEN:
    raise SystemExit("DISCORD_TOKEN not set.")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Simple FTC Scout API client
API_BASE = "https://api.ftcscout.org/rest/v1"

# Shared requests session with retry/backoff and Retry-After support to handle rate limits
SESSION = requests.Session()
retry_strategy = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["GET", "POST"]),
    respect_retry_after_header=True,
)
adapter = HTTPAdapter(max_retries=retry_strategy)
SESSION.mount("https://", adapter)
SESSION.mount("http://", adapter)

# Simple in-memory TTL cache: key -> (expiry_epoch, value)
CACHE: Dict[str, object] = {}

def cache_get(key: str):
    entry = CACHE.get(key)
    if not entry:
        return None
    expiry, value = entry
    if time.time() > expiry:
        CACHE.pop(key, None)
        return None
    return value

def cache_set(key: str, value: object, ttl: int = 60):
    CACHE[key] = (time.time() + ttl, value)


def api_get(path: str, params: dict | None = None, ttl: int = 60):
    """GET JSON from the FTC Scout REST API with caching and basic Retry-After handling.
    Returns (data, error) where error is None on success.
    """
    url = API_BASE + path
    key = f"api:{url}:{params}"
    cached = cache_get(key)
    if cached is not None:
        return cached, None

    try:
        resp = SESSION.get(url, params=params, timeout=10)
    except requests.RequestException as e:
        return None, f"Request failed: {e}"

    if resp.status_code == 429:
        ra = resp.headers.get("Retry-After")
        try:
            wait = int(float(ra)) if ra else 5
        except Exception:
            wait = 5
        time.sleep(wait)
        try:
            resp = SESSION.get(url, params=params, timeout=10)
        except requests.RequestException as e:
            return None, f"Request failed after retry: {e}"

    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"

    try:
        data = resp.json()
    except Exception as e:
        return None, f"Invalid JSON: {e}"

    cache_set(key, data, ttl=ttl)
    return data, None


def toa_get(path: str, params: dict | None = None, ttl: int = 60):
    """GET JSON from The Orange Alliance API (if TOA_KEY present). Returns (data, error)."""
    if not TOA_KEY:
        return None, "TOA key not configured"
    url = TOA_API_BASE + path
    key = f"toa:{url}:{params}"
    cached = cache_get(key)
    if cached is not None:
        return cached, None

    headers = {"X-TOA-Key": TOA_KEY}
    try:
        resp = SESSION.get(url, params=params, headers=headers, timeout=10)
    except requests.RequestException as e:
        return None, f"TOA request failed: {e}"

    if resp.status_code == 429:
        ra = resp.headers.get("Retry-After")
        try:
            wait = int(float(ra)) if ra else 5
        except Exception:
            wait = 5
        time.sleep(wait)
        try:
            resp = SESSION.get(url, params=params, headers=headers, timeout=10)
        except requests.RequestException as e:
            return None, f"TOA request failed after retry: {e}"

    if resp.status_code != 200:
        return None, f"TOA HTTP {resp.status_code}"

    try:
        data = resp.json()
    except Exception as e:
        return None, f"TOA invalid JSON: {e}"

    cache_set(key, data, ttl=ttl)
    return data, None


def parse_quick_stats_from_soup(soup: BeautifulSoup) -> Optional[Dict]:
    """Try to find and parse a Quick Stats table from the team page soup.
    Returns a dict with keys: 'categories': List[str], 'rows': Dict[label, List[str]]
    or None if no suitable table found.
    """

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
    """Search for events. Usage: `!events [search]` — e.g., `!events michigan`"""
    params = {"limit": 8}
    if query:
        params["searchText"] = query

    data, err = api_get("/events/search", params=params)
    if err:
        await ctx.send(
            f"⚠️ **Event search temporarily unavailable**\n"
            f"The FTC Scout event endpoints are currently down.\n"
            f"You can still use `!team <team_number>` to get team stats.\n"
            f"Error: {err}"
        )
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
    """Get event details. Usage: `!event <code> [season]` — e.g., `!event USCMIW` or `!event USCMIW 2025`"""
    if season is None:
        season = datetime.now().year

    path = f"/events/{season}/{code}"
    data, err = api_get(path)
    if err:
        await ctx.send(
            f"⚠️ **Event lookup temporarily unavailable**\n"
            f"The FTC Scout event endpoints are currently down.\n"
            f"You can still use `!team <team_number>` to get team stats.\n"
            f"Error: {err}"
        )
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
    """List teams at an event."""
    await ctx.send(
        "⚠️ **Team lookup by event temporarily unavailable**\n"
        "The FTC Scout event endpoints are currently down.\n"
        "You can still use `!team <team_number>` to get individual team stats.\n"
        "We'll re-enable `!teams` when FTC Scout restores service."
    )


@bot.command(name="team")
async def team(ctx, team_number: int):
    """Get basic team info and Quick Stats. Usage: `!team <team_number>` — e.g., `!team 22565`"""
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
        html_resp = SESSION.get(html_url, timeout=10)
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


@bot.command(name="team-events")
async def team_events(ctx, team_number: int, season: int | None = None):
    """Get all event participations for a team in a season. Usage: `!team-events <team_number> [season]`"""
    if season is None:
        season = datetime.now().year
    
    path = f"/teams/{team_number}/events/{season}"
    data, err = api_get(path)
    if err:
        await ctx.send(
            f"⚠️ **Event lookup temporarily unavailable**\n"
            f"The FTC Scout event endpoints are currently down.\n"
            f"Error: {err}"
        )
        return
    
    if not data:
        await ctx.send(f"No event participations found for team {team_number} in season {season}.")
        return
    
    lines = [f"**Team {team_number} — Events ({season})**\n"]
    for event in data[:20]:
        event_code = event.get("eventCode", "?")
        event_name = event.get("eventName", "?")
        lines.append(f"{event_code} — {event_name}")
    
    await ctx.send("\n".join(lines)[:1900])


@bot.command(name="team-awards")
async def team_awards(ctx, team_number: int, season: int | None = None, event_code: str | None = None):
    """Get awards for a team (optionally filtered by season or event). Usage: `!team-awards <team_number> [season] [event_code]`"""
    params = {}
    if season:
        params["season"] = season
    if event_code:
        params["eventCode"] = event_code
    
    path = f"/teams/{team_number}/awards"
    data, err = api_get(path, params=params)
    if err:
        await ctx.send(f"Error: {err}")
        return
    
    if not data:
        await ctx.send(f"No awards found for team {team_number}.")
        return
    
    filters = []
    if season:
        filters.append(f"season {season}")
    if event_code:
        filters.append(f"event {event_code}")
    filter_str = f" ({', '.join(filters)})" if filters else ""
    
    lines = [f"**Team {team_number} — Awards{filter_str}**\n"]
    for award in data[:20]:
        # Award name is in 'type' field
        award_name = award.get("type", "?")
        season_val = award.get("season", "?")
        event = award.get("eventCode", "?")
        placement = award.get("placement", "?")
        
        # Convert placement number to ordinal (1st, 2nd, 3rd, etc.)
        if placement and isinstance(placement, int):
            if placement % 100 in (11, 12, 13):
                ordinal = f"{placement}th"
            elif placement % 10 == 1:
                ordinal = f"{placement}st"
            elif placement % 10 == 2:
                ordinal = f"{placement}nd"
            elif placement % 10 == 3:
                ordinal = f"{placement}rd"
            else:
                ordinal = f"{placement}th"
        else:
            ordinal = str(placement)
        
        lines.append(f"{award_name} — {ordinal} Place ({event}, {season_val})")
    
    await ctx.send("\n".join(lines)[:1900])


@bot.command(name="team-matches")
async def team_matches(ctx, team_number: int, season: int | None = None, event_code: str | None = None):
    """Get matches for a team (optionally filtered by season or event). Usage: `!team-matches <team_number> [season] [event_code]`"""
    params = {}
    if season:
        params["season"] = season
    if event_code:
        params["eventCode"] = event_code
    
    path = f"/teams/{team_number}/matches"
    data, err = api_get(path, params=params)
    if err:
        await ctx.send(f"Error: {err}")
        return
    
    if not data:
        await ctx.send(f"No matches found for team {team_number}.")
        return
    
    filters = []
    if season:
        filters.append(f"season {season}")
    if event_code:
        filters.append(f"event {event_code}")
    filter_str = f" ({', '.join(filters)})" if filters else ""
    
    lines = [f"**Team {team_number} — Matches{filter_str}**\n"]
    for match in data[:15]:
        match_num = match.get("matchNumber", "?")
        event = match.get("eventCode", "?")
        result = match.get("result", "?")
        lines.append(f"Match {match_num} ({event}): {result}")
    
    await ctx.send("\n".join(lines)[:1900])


@bot.command(name="team-stats")
async def team_stats(ctx, team_number: int, season: int | None = None, region: str | None = None):
    """Get quick stats for a team in a season/region. Usage: `!team-stats <team_number> [season] [region]`"""
    if season is None:
        season = datetime.now().year
    
    params = {"season": season}
    if region:
        params["region"] = region
    
    path = f"/teams/{team_number}/quick-stats"
    data, err = api_get(path, params=params)
    if err:
        await ctx.send(f"Error: {err}")
        return
    
    region_str = f" ({region})" if region else " (World)"
    embed = discord.Embed(
        title=f"Team {team_number} — Quick Stats {season}{region_str}",
        description="Statistics aggregated from all team matches this season."
    )
    
    # Add key stats as fields
    stats_to_show = [
        ("Ranking", "ranking"),
        ("Wins", "wins"),
        ("Losses", "losses"),
        ("Ties", "ties"),
        ("Avg OPR", "avgOpr"),
        ("Avg RP1", "avgRp1"),
        ("Avg RP2", "avgRp2"),
    ]
    
    for label, key in stats_to_show:
        value = data.get(key)
        if value is not None:
            embed.add_field(name=label, value=str(value), inline=True)
    
    await ctx.send(embed=embed)


@bot.command(name="teams-search")
async def teams_search(ctx, search_text: str | None = None, region: str | None = None, limit: int | None = None):
    """Search for teams. Usage: `!teams-search [search_text] [region] [limit]`"""
    params = {}
    if search_text:
        params["searchText"] = search_text
    if region:
        params["region"] = region
    if limit:
        params["limit"] = min(limit, 100)
    else:
        params["limit"] = 20
    
    path = "/teams/search"
    data, err = api_get(path, params=params)
    if err:
        await ctx.send(f"Error: {err}")
        return
    
    if not data:
        await ctx.send("No teams found.")
        return
    
    title = "Teams"
    if search_text:
        title += f" matching '{search_text}'"
    if region:
        title += f" in {region}"
    
    lines = [f"**{title}**\n"]
    for team in data:
        team_num = team.get("teamNumber")
        team_name = team.get("teamNickname", "")
        location = team.get("city", "")
        state = team.get("stateProv", "")
        lines.append(f"#{team_num} — {team_name} ({location} {state})".strip())
    
    await ctx.send("\n".join(lines)[:1900])


@bot.command(name="commands")
async def commands_list(ctx):
    """List all available bot commands with usage and short descriptions."""
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
        lines.append(f"**{usage}** — {desc}")

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