# Playbook config, sourced by scripts/*.sh. Copy to $PLAYBOOK_CONFIG
# (default: ~/.config/rove/playbook.conf.sh) and fill in. Plain bash. The values below are an
# EXAMPLE; this is the only file that names models or vendors.

# --- Tiers, by role. Any tier may be "none" (engine empty): routing then skips it. ---
#   swift:    cheap third-party lane. none here: the default tables carry no China-region hosted models; a project that wants OpenCode on
#             swift lists it in its own tier table, started by its Orca coordinator (model-routing.md, "Sandbox per tier")
#   standard: everyday lane
#   deep:     careful lane, reviews from tier B  top: rare second opinion, only via the machine coordinator
TIERS="swift standard deep top"
declare -A TIER_ENGINE=( [swift]=none [standard]=claude [deep]=claude [top]=claude )
declare -A TIER_CMD=(    [swift]="" [standard]="" [deep]="" [top]="" )   # engine launch command, "" = default
declare -A TIER_MODEL=(  [swift]="" [standard]="sonnet" [deep]="opus" [top]="<top-model-id>" )
declare -A TIER_BUDGET=( [swift]=none [standard]=main [deep]=main [top]=main )         # budget name, or none | manual

# --- Budgets: name -> usage feed and ceiling. A tier whose budget is none or manual (no feed)
# is never throttled by the scripts; its caps in the routing file are the only limit. ---
# Feed: a command printing "<weekly%> <window%>" (integers). Ceiling: a number (percent) or file:<path>
# to a JSON {"ceiling": <percent>} that the owner updates.
declare -A BUDGET_USAGE=(
  [main]="tail -60 \$HOME/.claude/usage-history.jsonl | jq -rs '(map(.weekly_reset)|max) as \$w | (map(.session_reset)|max) as \$s | \"\\((map(select(.weekly_reset==\$w).weekly)|max*100)|floor) \\((map(select(.session_reset==\$s).session)|max*100)|floor)\"'"   # max of recent samples: idle sessions log stale values
  [cheap]=""        # no feed: manual (watch the vendor console) or unlimited
)
# Reset feed: a command printing the short window's reset time as epoch seconds (reset times vary).
declare -A BUDGET_RESET=(
  [main]="tail -60 \$HOME/.claude/usage-history.jsonl | jq -s 'map(.session_reset)|max'"
)
declare -A BUDGET_CEILING=( [main]="file:$HOME/.config/claude-usage-ceiling.json" [cheap]="" )
DEFAULT_CEILING=40

# --- Mode file (day|night), written by the machine coordinator ---
MODE_FILE="${TMPDIR:-/tmp}/coordinator-mode"
NIGHT_START_DIR="${TMPDIR:-/tmp}/night-start"   # per-budget weekly snapshot at the start of the night

# --- Thresholds ---
STEP_NO_NEW=7           # weekly >= ceiling-7: no new work on the budget
STEP_WIP_STOP=4         # weekly >= ceiling-4: lanes push WIP and stop; coordinators write their handover
STEP_STOP_ALL=2         # weekly >= ceiling-2: everything on the budget stops
PRESSURE_MARGIN=15      # weekly >= ceiling-15: pressure notice only
MEM_LOW_GB=4            # window guard notifies the machine coordinator below this (2 polls in a row), once per episode
SWAP_HIGH_GB=12         # ... or above this much swap used
WINDOW_NIGHT_PCT=80     # night: no new lanes on a budget at this window use
NIGHT_MEM_MIN_GB=8      # night: no launches below this much available memory
HANDOVER_HINT="done, open, decisions for the owner, how to resume; in your handover memory, detailed enough for a new session"
WINDOW_WARN_PCT=95      # window guard: coordinators start nothing new
WINDOW_STOP_PCT=98      # window guard: limit imminent, lanes push WIP
POLL_SECS=30

# --- Project coordinators under Orca + Cotal: name -> the project's Cotal space (the slug of its name, `cotal space`).
# The window guard sends orders to these only, with `cotal send -s <space> -t coordinator`. ---
declare -A COORD_SPACE=(
  ["<PROJECT_A>"]="<project_a>"
)
# --- Rove coordinators: name -> Rove main task id; one engine tab for all. Read by the Rove-only scripts
# (night watch, stale-claim and park checks, docs review), never by the window guard. ---
declare -A COORD_TASK=(
  ["<PROJECT_A>"]="<COORDINATOR_TASK_ID_A>"
  ["<PROJECT_B>"]="<COORDINATOR_TASK_ID_B>"
)
COORD_TAB=tab-1
declare -A COORD_REPO=(   # project checkout, for scripts/stale-claims.sh
  ["<PROJECT_A>"]="$HOME/code/<project_a>"
)
declare -A COORD_SLOTS=( )   # optional: working lanes the night watch wants busy per project (default 1): it prods a coordinator with ready issues and fewer working lanes
# Optional: raise one project's weekly ceiling by N points (e.g. a project that mostly uses a cheap tier)
declare -A COORD_CEILING_ADD=( )

# --- Docs review launcher (runs on a tier: standard, since swift is none here) ---
DOCS_REVIEW_TIER=standard
BRIEF_DIR="$HOME/.config/rove"     # <PROJECT>-docs-review.md lives here
DOCS_REVIEW_WAIT=90                # seconds before checking the first prompt was submitted
# Run in the worktree; empty output = first prompt not submitted. Empty setting = always nudge once.
DOCS_REVIEW_SUBMITTED_CMD=""
