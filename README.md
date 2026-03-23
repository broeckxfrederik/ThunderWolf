# ThunderWolf

Discord bot for managing a sim racing team — automated member onboarding, race event lineups, car setup knowledge base, and role management. Built so leaders can focus on the community instead of admin.

---

## Features

| Feature | Description |
|---|---|
| **Persistent state** | All config, events, and requests are stored in SQLite and survive bot restarts and redeployments. |
| **Setup wizard** | `/setup` links the bot to your existing server roles and channels — no need to recreate or reassign anything. |
| **Member onboarding** | Private welcome channel per new member with role-picker buttons (Driver, Engineer, Livery Designer, Visitor, Updates Only). Auto-kicked after 12 hours if no response. |
| **Car list** | Managed list of cars with autocomplete. Each car gets a permanent setup forum thread — no duplicates, knowledge accumulates across seasons. |
| **Race events** | `/event` creates a dedicated race channel with car-slot buttons, TM lineup confirmation, and automated T-24h / T-1h reminders. Supports multiple entries of the same car (BMW #1, BMW #2, …). |
| **Role requests** | Members request role changes via `/role-request`. CEO/TM approve or deny via buttons; member is notified by DM. |
| **Reaction roles** | `/post-roles` posts an emoji-reaction message for opt-in notification roles. |

---

## 1. Create the Discord Bot

1. Go to [https://discord.com/developers/applications](https://discord.com/developers/applications) and click **New Application**.
2. Give it a name (e.g. `ThunderWolf`) and click **Create**.
3. In the left sidebar click **Bot**.
4. Under **Privileged Gateway Intents** enable all three:
   - `Presence Intent`
   - `Server Members Intent`
   - `Message Content Intent`
5. Click **Reset Token**, copy the token — you'll need it for `.env`.

---

## 2. Invite the Bot to Your Server

1. In the sidebar go to **OAuth2 → URL Generator**.
2. Under **Scopes** select `bot` **and** `applications.commands` (both are required).
3. Under **Bot Permissions** select:
   - `Manage Roles`
   - `Manage Channels`
   - `Kick Members`
   - `Send Messages`
   - `Read Messages / View Channels`
   - `Add Reactions`
   - `Manage Messages` (for pinning)
   - `Create Public Threads` (for car setup forum threads)
4. Copy the generated URL, open it in your browser, and invite the bot to your server.

---

## 3. Get Your Guild (Server) ID

1. In Discord, go to **User Settings → Advanced** and enable **Developer Mode**.
2. Right-click your server icon in the sidebar and click **Copy Server ID**.

---

## 4. Configure Environment Variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```env
DISCORD_TOKEN=your_bot_token_here
GUILD_ID=your_guild_id_here
```

---

## 5. Run with Docker (recommended)

Make sure you have [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/) installed.

```bash
docker compose up -d
```

The bot starts automatically and restarts on failure. The database is stored in `./data/thunderwolf.db` on the host — it is mounted as a volume so it survives `docker compose up --build`.

```bash
docker compose logs -f   # view logs
docker compose down      # stop
```

---

## 6. Run without Docker

Requires Python 3.11+.

```bash
pip install -r requirements.txt
cd bot
python bot.py
```

The database is created automatically at `bot/data/thunderwolf.db`.

---

## 7. First-time Setup in Discord

Once the bot is online, run these steps **in order**:

### Step 1 — Link roles and channels (CEO only)

```
/setup
```

This opens a step-through wizard that walks you through each role and channel the bot needs. For each item you can:
- **Pick an existing** server role or channel from a dropdown
- **Create a new one** with the default name (button)
- **Skip** to configure later

Run `/setup-status` at any time to see what is and isn't linked yet.

> After setup, go to **Server Settings → Roles** and drag the bot's role **above** all team roles so it can assign them.

### Step 2 — Add cars to the list (CEO / Team Manager)

```
/car-add name:BMW M4 GT3
/car-add name:Ferrari 499P
```

Each car gets a permanent thread in your car-setups forum channel. These threads are the team's shared setup knowledge base — they are never deleted.

### Step 3 — Post the reaction-role selector (CEO / Team Manager)

```
/post-roles
```

Posts a pinned emoji-reaction message for opt-in notification roles.

### Step 4 — Test the welcome flow (CEO / Team Manager)

```
/test-welcome user:@SomeMember
```

---

## Slash Commands

| Command | Who | Description |
|---|---|---|
| `/setup` | CEO | Step-through wizard to link existing roles/channels (or create new ones). Saves to DB. |
| `/setup-status` | CEO, Team Manager | Show current role/channel mappings with ✅/❌. |
| `/car-add name:…` | CEO, Team Manager | Add a car to the guild list and create its setup forum thread. |
| `/car-remove name:…` | CEO, Team Manager | Remove a car from the list (its thread is kept). |
| `/car-list` | Everyone | Show all registered cars with links to their setup threads. |
| `/event name:… date:… time:… cars:…` | CEO, Team Manager | Create a race event channel with car-slot buttons and lineup management. Cars autocomplete from the car list. Repeat a car name for multiple slots. |
| `/lineup-set driver:@user slot:…` | CEO, Team Manager | Override a driver's slot before the lineup is confirmed. |
| `/event-result` | CEO, Team Manager | Post a results embed in the current race channel. |
| `/role-request role:…` | Everyone | Request a team role change (Driver, Engineer, Livery Designer, …). Posts a card in #role-requests for CEO/TM to approve or deny. |
| `/post-roles` | CEO, Team Manager | Post the opt-in reaction-role message. |
| `/test-welcome user:@Member` | CEO, Team Manager | Trigger the welcome channel flow for a member. |

---

## How Race Events Work

1. TM runs `/event name:Monza date:2026-03-28 time:20:00 cars:BMW M4 GT3, Ferrari 499P`
   - Autocomplete suggests car names as you type
   - Repeat a name (e.g. `BMW M4 GT3, BMW M4 GT3`) to create two slots for the same car
2. Bot creates a `#race-monza` channel with car-slot buttons
3. Drivers click their slot — they can change it anytime before confirmation
4. TM reviews the draft and clicks **✅ Confirm Lineup** — lineup is locked and pinned
5. Bot sends a T-24h reminder and a T-1h reminder automatically in the race channel
6. After the race, TM runs `/event-result` to post the final standings

---

## How Onboarding Works

1. Member joins → bot creates a private `#welcome-<name>` channel visible to the member, CEO, and Team Manager
2. Member picks their role: **Driver**, **Engineer**, **Livery Designer**, **Visitor**, or **Updates Only**
3. Role is assigned instantly and the channel is deleted
4. If no pick within **12 hours**, the member is automatically kicked

---

## Configuration

Tunable defaults are in `bot/config.py`. If a role or channel doesn't exist in the server yet, the bot falls back to these names when creating new ones via `/setup`.

| Constant | Default | Description |
|---|---|---|
| `ROLE_DRIVER` | `"Driver"` | Default name for the Driver role |
| `ROLE_ENGINEER` | `"Engineer"` | Default name for the Engineer role |
| `ROLE_LIVERY` | `"Livery-Designer"` | Default name for the Livery Designer role |
| `WELCOME_CATEGORY` | `"New Members"` | Default name for the welcome channels category |
| `RACES_CATEGORY` | `"Races"` | Default name for the race channels category |
| `CHANNEL_CAR_SETUPS` | `"car-setups"` | Default name for the car setup forum channel |
| `CHANNEL_ROLE_REQUESTS` | `"role-requests"` | Default name for the role requests channel |
| `WELCOME_TIMEOUT_HOURS` | `12` | Hours before a non-responding new member is kicked |
