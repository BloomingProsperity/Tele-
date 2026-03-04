# Tele AI Translator

Telegram + Discord translator.

## Recommended Translation Models (NVIDIA API)

- Primary: `moonshotai/kimi-k2-instruct-0905`
- Fallback: `moonshotai/kimi-k2-instruct`

## Quick Start

1. Fill `.env` values:
- `RUN_MODE=bot`
- `BOT_TOKEN` (from BotFather)
- `NVIDIA_API_KEY` (already configured if you keep local `.env`)
- `INCOMING_TRANSLATION_OUTPUT_MODE=saved_messages` (userbot incoming translations visible only to you)

2. Optional for userbot mode:
- set `RUN_MODE=userbot`
- fill `TG_API_ID`, `TG_API_HASH`

3. Install dependencies:
- `uv sync --extra dev`
4. Run:
- `uv run python -m tele_ai.main`

## Discord Quick Start

1. In `.env`, set:
- `DISCORD_BOT_TOKEN=...`
- `NVIDIA_API_KEY=...`
- optional: `DISCORD_COMMAND_PREFIX=!`
- optional: `DISCORD_OWNER_ID=<your_discord_user_id>` (limit admin commands to you)

2. In Discord Developer Portal, enable:
- `MESSAGE CONTENT INTENT`

3. Invite bot to server with message read/send permissions, then run:
- `uv run discord-ai`

4. Discord commands:
- `!ai_pause`
- `!ai_resume`
- `!ai_status`
- `!tr <target_lang> <text>`
- reply to a message with `!tr` (auto target) or `!tr en`

## Commands

- `/ai_pause`: pause auto translation
- `/ai_resume`: resume auto translation
- `/ai_status`: show running status and provider stats
- `/tr`: reply to a message and translate it
- `/tr <lang>`: reply to a message and force target language (example: `/tr en`)

## Incoming Privacy (Userbot)

- `INCOMING_TRANSLATION_OUTPUT_MODE=saved_messages`: send incoming translations to your Saved Messages (private, recommended)
- `INCOMING_TRANSLATION_OUTPUT_MODE=same_chat`: send translation in current chat (the other side can see)
- `INCOMING_TRANSLATION_OUTPUT_MODE=off`: disable incoming auto-output

## Security

- **NEVER commit `.env` to version control.** The `.gitignore` already excludes it.
- If this project folder is synced via OneDrive/Dropbox/Google Drive, your API keys are stored in the cloud. Consider moving `.env` outside the synced folder or use a secrets manager.
- `.env.example` contains safe placeholder values for reference.
- Rotate `BOT_TOKEN` and `NVIDIA_API_KEY` if they may have been exposed.

## Low-Memory VPS Tips (2C2G)

- `LANG_HISTORY_RETENTION_HOURS=24` keeps only the last 24h language history in SQLite.
- `STATE_CLEANUP_INTERVAL_MINUTES=60` runs periodic cleanup every hour.
- Cache controls (RAM):
  - `PROCESSED_CACHE_MAXSIZE=20000`
  - `PROCESSED_CACHE_TTL_SECONDS=600`
  - `FAILURE_NOTICE_MAXSIZE=2000`
  - `FAILURE_NOTICE_TTL_SECONDS=120`

## Deploy To VPS (Ubuntu + systemd)

1. Upload this project to your VPS, for example to `/opt/tele-ai`.
2. Edit `.env` on VPS:
- `RUN_MODE=bot`
- `BOT_TOKEN=<your_bot_token>`
- `NVIDIA_API_KEY=<your_nvidia_key>`
- Keep model defaults unless you want to change them.
3. Run deployment script on VPS:
- `cd /opt/tele-ai`
- `chmod +x deploy/vps/install_systemd.sh`
- `./deploy/vps/install_systemd.sh`
4. Check runtime:
- `sudo systemctl status tele-ai-bot --no-pager`
- `sudo journalctl -u tele-ai-bot -f`
