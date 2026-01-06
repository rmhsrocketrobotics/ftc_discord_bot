# FTC Discord Bot

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

