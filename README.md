# ThunderWolf

Discord bot for managing a racing team — member onboarding, race event lineups, and opt-in notification roles.

---

## Features

| Feature | Description |
|---|---|
| **Welcome flow** | Private channel created for each new member with role-picker buttons. Auto-reminder after 2 days, auto-kick after 7 days of no response. |
| **Racer onboarding** | Dedicated private channel for CEO + Team Manager to onboard new racers. |
| **Race events** | `/event` posts a car-selection message; picks update a live lineup in the TM channel. |
| **Reaction roles** | `/post-roles` posts an emoji-reaction message for opt-in notification roles. |

---

## 1. Create the Discord Bot

1. Go to [https://discord.com/developers/applications](https://discord.com/developers/applications) and click **New Application**.
2. Give it a name (e.g. `ThunderWolf`) and click **Create**.
3. In the left sidebar click **Bot**, then **Add Bot**.
4. Under **Privileged Gateway Intents** enable all three:
   - `Presence Intent`
   - `Server Members Intent`
   - `Message Content Intent`
5. Click **Reset Token**, copy the token — you'll need it for `.env`.

---

## 2. Invite the Bot to Your Server

1. In the sidebar go to **OAuth2 → URL Generator**.
2. Under **Scopes** select `bot` and `applications.commands`.
3. Under **Bot Permissions** select:
   - `Manage Roles`
   - `Manage Channels`
   - `Kick Members`
   - `Send Messages`
   - `Read Messages / View Channels`
   - `Add Reactions`
   - `Manage Messages` (for pinning)
4. Copy the generated URL, open it in your browser, and invite the bot to your server.

---

## 3. Get Your Guild (Server) ID

1. In Discord, go to **User Settings → Advanced** and enable **Developer Mode**.
2. Right-click your server icon in the sidebar and click **Copy Server ID**.

---

## 4. Create the Required Roles

Create these roles in your Discord server (**Server Settings → Roles**). The names must match exactly:

| Role name | Purpose |
|---|---|
| `CEO` | Full admin access to all bot commands |
| `Team-Manager` | Can run `/event`, `/test-welcome`, `/post-roles` |
| `Racer` | Assigned to members who join as a racer |
| `Visitor` | Assigned to members who are just visiting |
| `Updates-Only` | Assigned to members who only want updates |
| `Livery-Prodigy` | Opt-in via reaction roles |
| `F1-Updates` | Opt-in via reaction roles |
| `Twitch-Notifications` | Opt-in via reaction roles |
| `Driver-Notification` | Opt-in via reaction roles |

> The bot role must be placed **above** all the roles it needs to assign in the role hierarchy.

---

## 5. Configure Environment Variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```env
DISCORD_TOKEN=your_bot_token_here
GUILD_ID=your_guild_id_here
```

---

## 6. Run with Docker (recommended)

Make sure you have [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/) installed.

```bash
docker compose up -d
```

The bot will start automatically and restart on failure. To check logs:

```bash
docker compose logs -f
```

To stop:

```bash
docker compose down
```

---

## 7. Run without Docker

Requires Python 3.11+.

```bash
pip install -r requirements.txt
cd bot
python bot.py
```

---

## 8. First-time Setup in Discord

Once the bot is online, run these commands in your server:

1. **Post the reaction-role selector** in any channel:
   ```
   /post-roles
   ```

2. **Test the welcome flow** with yourself or another member:
   ```
   /test-welcome user:@SomeMember
   ```

The **"New Members"** category will be created automatically on first join. It is only visible to the guild owner and the `CEO` role.

---

## Configuration

All tunable values are in `bot/config.py`:

| Constant | Default | Description |
|---|---|---|
| `WELCOME_CATEGORY` | `"New Members"` | Category name for welcome channels |
| `WELCOME_REMINDER_DAYS` | `2` | Days before a reminder is sent to non-responders |
| `WELCOME_KICK_DAYS` | `7` | Days before a non-responder is kicked |
| `RACER_REMINDER_DAYS` | `2` | Days before CEO is pinged if Team Manager hasn't responded in a racer channel |
| `RACER_ONBOARDING_MSG` | *(see config)* | Message template posted in new racer channels |

---

## Slash Commands

| Command | Who can use it | Description |
|---|---|---|
| `/event cars:Ferrari,Mercedes` | CEO, Team Manager | Start a race event with car-selection buttons |
| `/post-roles` | CEO, Team Manager | Post the opt-in reaction-role message |
| `/test-welcome user:@Member` | CEO, Team Manager | Trigger the welcome channel flow for a member |
