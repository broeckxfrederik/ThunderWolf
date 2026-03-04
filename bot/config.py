# ── Membership roles ──────────────────────────────────────────────────────────
ROLE_RACER        = "Racer"
ROLE_VISITOR      = "Visitor"
ROLE_UPDATES      = "Updates-Only"

# ── Reaction / opt-in roles ───────────────────────────────────────────────────
ROLE_LIVERY       = "Livery-Prodigy"
ROLE_F1           = "F1-Updates"
ROLE_TWITCH       = "Twitch-Notifications"
ROLE_DRIVER       = "Driver-Notification"

# ── Admin roles ───────────────────────────────────────────────────────────────
ROLE_CEO          = "CEO"
ROLE_TEAM_MANAGER = "Team-Manager"

# ── Channels ──────────────────────────────────────────────────────────────────
CHANNEL_LINEUP    = "team-manager-lineups"   # private channel for race lineups

# ── Temp race role prefix ─────────────────────────────────────────────────────
# Temp roles are named  Race-<car>  and wiped at the start of each /event
RACE_ROLE_PREFIX  = "Race-"

# ── Reaction-role emoji map  (emoji → role name) ──────────────────────────────
REACTION_ROLES = {
    "🎨": ROLE_LIVERY,
    "📡": ROLE_F1,
    "📺": ROLE_TWITCH,
    "🏁": ROLE_DRIVER,
}

# ── Racer onboarding ──────────────────────────────────────────────────────────
# How many days before the bot pings @CEO if Team Manager hasn't responded
RACER_REMINDER_DAYS = 2

RACER_ONBOARDING_MSG = (
    "👋 Welcome to the team, {mention}!\n\n"
    "To get you set up as a racer, please:\n"
    "1. Add the following friend codes: **[add your friend codes here]**\n"
    "2. Share your in-game username\n"
    "3. Tell us your preferred racing style / availability\n\n"
    "When you're done, send a message here and tag {team_manager_mention} "
    "so they can complete your registration. ✅"
)
