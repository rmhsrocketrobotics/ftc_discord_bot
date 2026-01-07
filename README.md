# Discord FTC Scout Bot

A lightweight Discord bot that fetches FTC event and team data (from the FTC Scout REST API and optional The Orange Alliance API) and displays Quick Stats for teams inside rich Discord embeds.

## Features
- Commands: `!events`, `!event`, `!teams`, `!team`, `!commands`
- Parses the public team page to extract Quick Stats (table or div-grid layouts)
- Prefer The Orange Alliance (TOA) when `TOA_KEY` is set (optional)
- Simple request retries and in-memory TTL cache to reduce rate-limit issues
- Small Flask health endpoint for hosting platforms (Render, Railway, etc.)

## Requirements
- Python 3.11+ (3.13 tested in this workspace)
- Install dependencies:

```powershell
cd "C:\Users\krisi\OneDrive\Desktop\Coding\discord-bot"
python -m pip install -r requirements.txt
```

If you don't have a `requirements.txt`, install:

```powershell
python -m pip install discord.py requests beautifulsoup4 python-dotenv flask
```

## Environment
Create a `.env` file in the `discord-bot` folder with at least:

```
DISCORD_TOKEN=your_discord_bot_token
# Optional: prefer The Orange Alliance responses
TOA_KEY=your_toa_key
```

Make sure `discord-bot/.env` is listed in `.gitignore` (it is by default).

## Run locally

```powershell
cd "C:\Users\krisi\OneDrive\Desktop\Coding\discord-bot"
python bot.py
```

The bot will start and the Flask health endpoint will listen on port 4000 by default. If the bot logs `Logged in as <botname>` it connected successfully.

## Commands
- `!events [search]` — Search events (up to 8 results)
- `!event <code> [season]` — Get event details
- `!teams <event_code> [season]` — List teams at an event
- `!team <team_number>` — Show team basic info and Quick Stats (embed)
- `!commands` — List all available commands and usage

Notes:
- `!team` will prefer TOA when `TOA_KEY` is configured; otherwise it falls back to scraping ftcscout.org team pages.
- Outputs are shown as embeds with no external links by design.

## Deploying on Render (quick)
1. Push your repo to GitHub.
2. Create a new **Background Worker** on Render.
3. Connect your GitHub repo and select the branch.
4. Set the start command to:

```text
python -u bot.py
```

5. Add environment variables in the Render dashboard: `DISCORD_TOKEN` (required) and `TOA_KEY` (optional).
6. Deploy and check logs for `Logged in as ...`.

## Troubleshooting
- If the bot fails to login, ensure `DISCORD_TOKEN` is valid and `MESSAGE CONTENT INTENT` is enabled in your Discord developer app for the bot.
- If `!team` doesn't show Quick Stats, the site structure may have changed — open an issue and include the team page HTML.
- Use logs on Render (or local console) to see HTTP errors and Retry-After handling messages.

## Contributing / Next steps
- Add a `--debug` or `!team --debug` flag to show chosen parse block and raw HTML when parsing fails.
- Convert blocking HTTP calls to async (aiohttp) for improved concurrency.
- Add persistent caching (Redis) for multi-process deployments.

---
Created/maintained in-repo for quick reference: `discord-bot/bot.py`.
# Minimal Discord Bot

This folder contains a minimal Discord bot using discord.py.

Files:
- `bot.py` — minimal bot that responds to `!ping`, `!echo`, and replies to `hello`.
- `requirements.txt` — dependencies.
- `.env.example` — example environment file (do NOT commit your real token).

Getting started
1. Create a Discord application and bot at https://discord.com/developers/applications
   - Add a Bot to the application and copy the token.
   - Under "Privileged Gateway Intents" enable "Message Content Intent" if you want the bot to read message content.
2. Copy `.env.example` to `.env` and set `DISCORD_TOKEN=your_token_here`.
3. Install dependencies (recommended to use a virtualenv):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

4. Run the bot:

```powershell
python bot.py
```

5. Invite the bot to a server using the OAuth2 URL generator in the Developer Portal (scopes: bot; permissions: Send Messages, Read Messages/View Channels). If using slash commands later, include `applications.commands` scope.

Notes
- Never commit your real token. Use `.env` and keep it out of version control.
- This is a minimal starting point. Consider adding commands, error handling, logging, and tests.
