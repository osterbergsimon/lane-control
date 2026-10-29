# Playbook config, sourced by scripts/*.sh. Copy to $PLAYBOOK_CONFIG
# (default: ~/.config/rove/playbook.conf.sh) and fill in. Plain bash. The values below are an
# EXAMPLE; this is the only file that names models or vendors.

# --- Tiers, by role. Any tier may be "none" (engine empty): routing then skips it. ---
#   swift:    cheap third-party lane            standard: everyday lane
#   deep:     careful lane, reviews from tier B  top: rare second opinion, only via the machine coordinator
TIERS="swift standard deep top"
declare -A TIER_ENGINE=( [swift]=opencode [standard]=claude [deep]=claude [top]=claude )
declare -A TIER_CMD=(    [swift]="opencode --auto" [standard]="" [deep]="" [top]="" )   # engine launch command, "" = default
declare -A TIER_MODEL=(  [swift]="<cheap-model-id>" [standard]="sonnet" [deep]="opus" [top]="<top-model-id>" )
declare -A TIER_BUDGET=( [swift]=cheap [standard]=main [deep]=main [top]=main )         # budget name, or none | manual

# --- Budgets: name -> usage feed and ceiling. A tier whose budget is none or manual (no feed)
# is never throttled by the scripts; its caps in the routing file are the only limit. ---
# Feed: a command printing "<weekly%> <window%>" (integers). Ceiling: a number (percent) or file:<path>
# to a JSON {"ceiling": <percent>} that the owner updates.
declare -A BUDGET_USAGE=(
  [main]="tail -1 \$HOME/.claude/usage-history.jsonl | jq -r '\"\\((.weekly*100)|floor) \\((.session*100)|floor)\"'"
  [cheap]=""        # no feed: manual (watch the vendor console) or unlimited
)
declare -A BUDGET_CEILING=( [main]="file:$HOME/.config/claude-usage-ceiling.json" [cheap]="" )
DEFAULT_CEILING=40

# --- Mode file (day|night), written by the machine coordinator ---
MODE_FILE="${TMPDIR:-/tmp}/coordinator-mode"
NIGHT_START_DIR="${TMPDIR:-/tmp}/night-start"   # per-budget weekly snapshot at the start of the night

# --- Thresholds ---
PRESSURE_MARGIN=15      # weekly >= ceiling-15: pressure signal, no night launches
MEM_LOW_GB=4            # watch wakes the machine coordinator below this (2 polls in a row)
SWAP_HIGH_GB=12         # ... or above this much swap used
WINDOW_WATCH_PCT=90     # watch wakes at this window use of a metered budget
WINDOW_NIGHT_PCT=80     # night: no new lanes on a budget at this window use
NIGHT_MEM_MIN_GB=8      # night: no launches below this much available memory
POLL_SECS=30

# --- Project coordinators: name -> Rove main task id; one engine tab for all ---
declare -A COORD_TASK=(
  ["<PROJECT_A>"]="<COORDINATOR_TASK_ID_A>"
  ["<PROJECT_B>"]="<COORDINATOR_TASK_ID_B>"
)
COORD_TAB=tab-1

# --- Docs review launcher (runs on a tier, normally swift) ---
DOCS_REVIEW_TIER=swift
BRIEF_DIR="$HOME/.config/rove"     # <PROJECT>-docs-review.md lives here
DOCS_REVIEW_WAIT=90                # seconds before checking the first prompt was submitted
# Run in the worktree; empty output = first prompt not submitted. Empty setting = always nudge once.
DOCS_REVIEW_SUBMITTED_CMD="opencode session list"
