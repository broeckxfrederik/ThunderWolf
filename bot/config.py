# ── Team / membership roles ───────────────────────────────────────────────────
# These are the default names used when creating new roles.
# Existing roles are linked via /setup (IDs stored in DB).
ROLE_DRIVER       = "Driver"
ROLE_ENGINEER     = "Engineer"
ROLE_LIVERY       = "Livery-Designer"
ROLE_VISITOR      = "Visitor"
ROLE_UPDATES      = "Updates-Only"

# ── Admin roles ───────────────────────────────────────────────────────────────
ROLE_CEO          = "CEO"
ROLE_TEAM_MANAGER = "Team-Manager"

# ── Reaction / opt-in roles ───────────────────────────────────────────────────
ROLE_F1           = "F1-Updates"
ROLE_TWITCH       = "Twitch-Notifications"
ROLE_DRIVER_NOTIF = "Driver-Notification"

# ── Reaction-role emoji map  (emoji → role name) ──────────────────────────────
REACTION_ROLES = {
    "📡": ROLE_F1,
    "📺": ROLE_TWITCH,
    "🏁": ROLE_DRIVER_NOTIF,
}

# ── Temp race role prefix ─────────────────────────────────────────────────────
RACE_ROLE_PREFIX = "Race-"

# ── Default channel / category names (used when creating new ones via /setup) ─
WELCOME_CATEGORY        = "New Members"
RACES_CATEGORY          = "Races"
ADMIN_CATEGORY          = "Admin"
CHANNEL_LINEUP          = "team-manager-lineups"
CHANNEL_ROLE_REQUESTS   = "role-requests"
CHANNEL_ROLE_APPROVALS  = "role-approvals"
CHANNEL_LEAVERS         = "leavers"

# ── Welcome reminder ──────────────────────────────────────────────────────────
# Days between reminder messages sent to members who haven't picked a role
WELCOME_REMINDER_DAYS = 7

# ── Setup wizard config keys (stored in guild_config table) ───────────────────
# Roles
CFG_ROLE_DRIVER     = "role_driver"
CFG_ROLE_ENGINEER   = "role_engineer"
CFG_ROLE_LIVERY     = "role_livery"
CFG_ROLE_VISITOR    = "role_visitor"
CFG_ROLE_UPDATES    = "role_updates"
CFG_ROLE_CEO        = "role_ceo"
CFG_ROLE_TM         = "role_tm"
# Channels / categories
CFG_CAT_WELCOME     = "category_welcome"
CFG_CAT_RACES       = "category_races"
CFG_CAT_ADMIN       = "category_admin"
CFG_CH_ROLE_REQ     = "channel_role_requests"
CFG_CH_ROLE_APPROVALS = "channel_role_approvals"
CFG_CH_LINEUP       = "channel_lineup"
CFG_CH_LEAVERS      = "channel_leavers"
