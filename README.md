# Otacon-Bot
Productivity bot I've designed for securely interacting with my PC via Telegram.

A private, single-user Telegram bot that spawns `tmux` sessions running
`claude` (Claude Code) in specific project directories on this machine.
The bot itself is only a spawner/trigger — once a session exists, you
continue it from the Claude mobile app via Remote Control. Only one
Telegram user id (yours) will ever get a response; everyone else is
silently ignored.

## v1 commands

- `/start` — confirms the bot is online.
- `/new <project>` — spawns a new tmux session running `claude` in the
  allowlisted project's directory. Replies with the session name.
- `/sessions` — lists tmux sessions this bot spawned (not all tmux
  sessions on the machine).
- `/kill <session>` — kills a session, but only one this bot spawned.

Allowed directories are configured in `bot_config.json` at the repo root
(copy `bot_config.example.json` to get started), under the
`allowed_dirs` field — add an entry there (name → absolute path) for
every directory you want reachable from Telegram. `bot_config.json` is
gitignored since it contains real paths from your machine; only the
`.example` file with a placeholder path is committed. This is
intentionally not runtime-configurable from a chat command, and every
directory-touching operation in the bot validates against this list
before it acts (see `config.assert_dir_allowed` — a project-wide choke
point, not a check that lives only in the `/new` handler).

Not in v1 (deliberately out of scope): free-text prompt relay into a
running session, Discord, webhook mode, multi-user support.

---

## 1. Set up the bot with BotFather (you do this manually)

I did not create the bot or touch the Telegram API — this step is yours:

1. In Telegram, message **@BotFather** → `/newbot` → follow the prompts.
   Save the token it gives you; it goes in `.env` (below), never in code.
2. `/setjoingroups` → select your bot → **Disable**. This bot is for DMs
   only; there's no reason for it to ever be addable to a group.
3. `/setprivacy` → select your bot → **Enable**. In a group it would then
   only see messages that are commands, not general chat — belt-and-braces
   hygiene even though this bot should never end up in a group at all.

## 2. Find your numeric Telegram user id — without a third-party bot

You need your own numeric user id (not username) for `TELEGRAM_AUTHORIZED_USER_ID`.
Rather than trusting some `@userinfobot`-style third party with that
lookup, use the fact that this bot already logs the id of anyone who
messages it and gets rejected:

1. Copy `.env.example` to `.env` and fill in your real `TELEGRAM_BOT_TOKEN`.
   Leave `TELEGRAM_AUTHORIZED_USER_ID` as the placeholder `000000000` for now.
2. Run the bot locally: `python3 -m otacon_bot.main` (from the repo root,
   with the venv from step 3 below active).
3. From your own Telegram account, send `/start` to your bot. You'll get
   no reply (expected — the placeholder id doesn't match you), but the
   bot's console log will print a line like:
   `Rejected update from unauthorized user id=123456789`
4. Stop the bot (Ctrl-C), put that number into `TELEGRAM_AUTHORIZED_USER_ID`
   in `.env`, and restart. `/start` should now reply "Otacon online."

## 3. Install and run locally

```bash
brew install tmux            # skip if already installed
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env         # then fill in TELEGRAM_BOT_TOKEN (and see step 2 for the user id)
cp bot_config.example.json bot_config.json   # then edit allowed_dirs with your real project name -> path entries
python3 -m otacon_bot.main
```

The bot uses long polling (`run_polling`), so no inbound port needs to be
opened on this machine and no webhook/TLS setup is required for normal use.

## 4. Running persistently

Pick one:

### Option A — tmux (simplest)

```bash
tmux new -s otacon-bot-daemon
cd /path/to/Otacon-Bot     # wherever you cloned this repo
source .venv/bin/activate
python3 -m otacon_bot.main
# detach with Ctrl-b d — the bot keeps running
```

Reattach any time with `tmux attach -t otacon-bot-daemon` to check logs.
Note this is a *different* tmux session from the ones the bot itself
spawns for Claude Code — nothing conflicts, since spawned sessions are
always named `otacon-<project>-<timestamp>`.

### Option B — launchd (survives reboots, starts at login)

Create `~/Library/LaunchAgents/com.otacon.bot.plist`, replacing
`/path/to/Otacon-Bot` below with wherever you actually cloned this repo:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.otacon.bot</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/Otacon-Bot/.venv/bin/python3</string>
        <string>-m</string>
        <string>otacon_bot.main</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/Otacon-Bot</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/path/to/Otacon-Bot/otacon-bot.log</string>
    <key>StandardErrorPath</key>
    <string>/path/to/Otacon-Bot/otacon-bot.err.log</string>
</dict>
</plist>
```

Then:

```bash
launchctl load ~/Library/LaunchAgents/com.otacon.bot.plist
# to stop/unload later:
launchctl unload ~/Library/LaunchAgents/com.otacon.bot.plist
```

`python-dotenv` loads `.env` relative to the working directory, which
`WorkingDirectory` above pins correctly — no need to hardcode secrets
into the plist itself.

## Notes / non-goals

- I (the assistant) did not and will not create the bot via BotFather,
  register a webhook, or make any live Telegram API call — those require
  credentials only you should handle.
- Nothing in this repo was pushed to a remote; commits/pushes are left to
  you.
- `MAX_SESSIONS_PER_PROJECT` (default 3, in `config.py`) caps how many
  concurrent tmux sessions `/new` will spawn per project.
