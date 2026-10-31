#!/usr/bin/env bash
# =============================================================================
# configure.sh — write this node's .env and (re)start the netradar-node service
#
# The fusion center's setup.sh prints one block per node. Give it to this
# script in any of these ways:
#
#   ./configure.sh                       # paste the block, then press Ctrl-D
#   ./configure.sh --file node1.env      # read the block from a file
#   ./configure.sh --host <fusion-center-host> --node-id node1 --password <pw>
#                  [--group default] [--trigger mqtt|local] [--port 1883]
#
# Run it from your normal user account (it asks for sudo only to restart the
# service). Writes .env next to this script, readable only by you and root.
# =============================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$REPO_DIR/.env"

die()  { printf '\033[1;31m[configure] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }
warn() { printf '\033[1;33m[configure] WARNING:\033[0m %s\n' "$*" >&2; }
usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; }

declare -A V=(
  [MQTT_PORT]=1883
  [GROUP_ID]=default
  [NETRADAR_SCHEMA_VERSION]=1
  [NETRADAR_TOPIC_PREFIX]=netradar/v1
  [NETRADAR_TRIGGER_MODE]=mqtt
  [LOG_PORT]=5003
)

read_block() {  # KEY=VALUE lines; comments and blank lines ignored
  local line key val
  while IFS= read -r line || [[ -n $line ]]; do
    line="${line%%$'\r'}"
    [[ -z ${line//[[:space:]]/} || $line =~ ^[[:space:]]*# ]] && continue
    [[ $line == *=* ]] || die "not a KEY=VALUE line: '$line'"
    key="${line%%=*}"; val="${line#*=}"
    key="${key//[[:space:]]/}"; val="${val#"${val%%[![:space:]]*}"}"; val="${val%"${val##*[![:space:]]}"}"
    val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
    V[$key]="$val"
  done
}

FROM_ARGS=0
while (($#)); do
  case "$1" in
    --file)     [[ -f ${2:-} ]] || die "--file needs an existing file"; read_block < "$2"; FROM_ARGS=1; shift 2 ;;
    --host)     V[MQTT_HOST]="${2:?}"; FROM_ARGS=1; shift 2 ;;
    --node-id)  V[NODE_ID]="${2:?}"; FROM_ARGS=1; shift 2 ;;
    --password) V[MQTT_PASSWORD]="${2:?}"; FROM_ARGS=1; shift 2 ;;
    --group)    V[GROUP_ID]="${2:?}"; shift 2 ;;
    --trigger)  V[NETRADAR_TRIGGER_MODE]="${2:?}"; shift 2 ;;
    --port)     V[MQTT_PORT]="${2:?}"; shift 2 ;;
    -h|--help)  usage; exit 0 ;;
    *)          die "unknown option: $1 (see --help)" ;;
  esac
done

if ((FROM_ARGS == 0)); then
  echo "Paste the block for this node (from the fusion center's setup.sh), then press Ctrl-D:"
  read_block
fi

# ---- defaults derived from the node id, then validation ---------------------
[[ -n ${V[NODE_ID]:-} ]]       || die "NODE_ID is missing"
[[ -n ${V[MQTT_HOST]:-} ]]     || die "MQTT_HOST (fusion center) is missing"
[[ -n ${V[MQTT_PASSWORD]:-} ]] || die "MQTT_PASSWORD is missing"
: "${V[MQTT_USERNAME]:=${V[NODE_ID]}}"
: "${V[MQTT_CLIENT_ID]:=radar-${V[NODE_ID]}}"

[[ ${V[NODE_ID]} =~ ^[A-Za-z0-9_-]+$ ]] || die "NODE_ID may only contain letters, digits, '-' and '_'"
[[ ${V[NETRADAR_TRIGGER_MODE]} =~ ^(mqtt|local)$ ]] || die "trigger mode must be 'mqtt' or 'local'"
[[ ${V[MQTT_PORT]} =~ ^[0-9]+$ ]] || die "MQTT_PORT must be a number"
for k in "${!V[@]}"; do
  [[ ${V[$k]} != *[[:space:]#]* ]] || die "$k must not contain spaces or '#'"
  [[ ${V[$k]} != *"<"*">"* ]] || die "$k still contains a <placeholder>"
done

# ---- bind the node web pages to the Tailscale IP when available -------------
if [[ -z ${V[UI_BIND_HOST]:-} ]]; then
  if command -v tailscale >/dev/null 2>&1 && ts_ip="$(tailscale ip -4 2>/dev/null | head -n1)" && [[ -n $ts_ip ]]; then
    V[UI_BIND_HOST]="$ts_ip"
  else
    V[UI_BIND_HOST]="0.0.0.0"
    warn "Tailscale not found: node web pages (5001, 5003) will listen on every interface"
  fi
fi

# ---- write .env (owner-only) -------------------------------------------------
tmp="$(mktemp "$REPO_DIR/.env.XXXXXX")"
{
  echo "# Written by configure.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ). Do not commit."
  for k in MQTT_HOST MQTT_PORT NODE_ID MQTT_USERNAME MQTT_PASSWORD MQTT_CLIENT_ID GROUP_ID \
           NETRADAR_SCHEMA_VERSION NETRADAR_TOPIC_PREFIX NETRADAR_TRIGGER_MODE UI_BIND_HOST LOG_PORT; do
    echo "$k=${V[$k]}"
  done
} > "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$ENV_FILE"
[[ -n ${SUDO_USER:-} ]] && chown "$SUDO_USER" "$ENV_FILE"
echo "Wrote $ENV_FILE  (node ${V[NODE_ID]} → ${V[MQTT_HOST]}:${V[MQTT_PORT]}, web pages on ${V[UI_BIND_HOST]})"

# ---- reachability check (non-fatal) ------------------------------------------
if timeout 3 bash -c "</dev/tcp/${V[MQTT_HOST]}/${V[MQTT_PORT]}" 2>/dev/null; then
  echo "Fusion center broker reachable at ${V[MQTT_HOST]}:${V[MQTT_PORT]}"
else
  warn "cannot reach ${V[MQTT_HOST]}:${V[MQTT_PORT]} yet — is the fusion center up and on your tailnet?"
fi

# ---- (re)start the service -----------------------------------------------------
if systemctl list-unit-files netradar-node.service >/dev/null 2>&1 \
   && systemctl list-unit-files netradar-node.service | grep -q netradar-node; then
  echo "Restarting netradar-node service (may ask for your sudo password)…"
  sudo systemctl restart netradar-node
  echo "Follow its logs with: journalctl -u netradar-node -f"
else
  warn "netradar-node service not installed (run sudo ./install.sh first, or start manually)"
fi
