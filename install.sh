#!/usr/bin/env bash
# =============================================================================
# install.sh — one-time setup of a NetRadar radar node
#
# Target: NVIDIA Jetson Orin Nano, JetPack 6.x (Ubuntu 22.04), with a TI
# AWR2243BOOST radar board and a DCA1000EVM capture card.
#
# What it does (each step can be re-run safely):
#   1. apt packages (compilers, CMake, jsoncpp, FFTW, OpenCV, NetworkManager…)
#   2. FTDI D2XX driver from third_party/ (checksum-verified)
#   3. JETGPIO library (GPIO hardware trigger), pinned commit
#   4. Builds: setup_radar, DCA1000 CLI + libRF_API, trigger programs
#   5. Python virtual environment (.venv) + PyTorch (CPU build)
#   6. Dedicated network connection for the DCA1000 link + socket buffer sizes
#   7. systemd service "netradar-node" (enabled; started by configure.sh)
#
# Usage (from your normal user account, inside the repository):
#   sudo ./install.sh [--dca-iface enP8p1s0] [--skip-apt] [--skip-network]
#                     [--no-service]
#
# Next step afterwards: ./configure.sh   (see README.md)
# =============================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DCA_IFACE="enP8p1s0"
DCA_CONNECTION="netradar-dca1000"
DCA_HOST_CIDR="192.168.33.30/24"   # TI DCA1000 factory default (host side)
SKIP_APT=0
SKIP_NETWORK=0
INSTALL_SERVICE=1

FTDI_DIR="$REPO_DIR/third_party/ftdi-d2xx/linux-arm-v8"
FTDI_LIB="libftd2xx.so.1.4.34"
FTDI_SHA256="95c2f4b2d57c29bd7dd846df5c247347cc74ef92c36de72ac9def6707d741411"
JETGPIO_URL="https://github.com/Rubberazer/JETGPIO.git"
JETGPIO_COMMIT="c69401c4a798aa4ecd058696509dd01011a8976b"
TORCH_VERSION="2.10.0"

log()  { printf '\n\033[1;34m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[install] WARNING:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[install] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; }

while (($#)); do
  case "$1" in
    --dca-iface)    [[ $# -ge 2 ]] || die "--dca-iface needs an interface name"; DCA_IFACE="$2"; shift 2 ;;
    --skip-apt)     SKIP_APT=1; shift ;;
    --skip-network) SKIP_NETWORK=1; shift ;;
    --no-service)   INSTALL_SERVICE=0; shift ;;
    -h|--help)      usage; exit 0 ;;
    *)              die "unknown option: $1 (see --help)" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "run with sudo: sudo ./install.sh"
TARGET_USER="${SUDO_USER:-}"
[[ -n $TARGET_USER && $TARGET_USER != root ]] || die "run via sudo from your normal user account, not as root directly"
USER_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
as_user() { sudo -u "$TARGET_USER" -H "$@"; }

[[ $(uname -m) == aarch64 ]] || warn "this does not look like a Jetson (arch $(uname -m)); continuing anyway"

# --------------------------------------------------------------------------- 1
if ((SKIP_APT == 0)); then
  log "1/7 Installing apt packages"
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    build-essential cmake git pkg-config curl \
    libjsoncpp-dev libfftw3-dev libopencv-dev libeigen3-dev libfmt-dev \
    network-manager ethtool net-tools iputils-ping \
    python3-venv python3-pip python3-dev
else
  log "1/7 Skipping apt packages (--skip-apt)"
fi

# --------------------------------------------------------------------------- 2
log "2/7 Installing FTDI D2XX driver (unmodified, from third_party/)"
echo "$FTDI_SHA256  $FTDI_DIR/$FTDI_LIB" | sha256sum -c --quiet - \
  || die "checksum mismatch for $FTDI_LIB — refusing to install"
install -m 0755 "$FTDI_DIR/$FTDI_LIB" /usr/local/lib/
ln -sf "/usr/local/lib/$FTDI_LIB" /usr/local/lib/libftd2xx.so
install -m 0644 "$FTDI_DIR/ftd2xx.h" "$FTDI_DIR/WinTypes.h" /usr/local/include/
ldconfig

# --------------------------------------------------------------------------- 3
if [[ -f /usr/include/jetgpio.h || -f /usr/local/include/jetgpio.h ]]; then
  log "3/7 JETGPIO already installed — skipping"
else
  log "3/7 Building JETGPIO (commit ${JETGPIO_COMMIT:0:7})"
  tmp="$(mktemp -d)"
  git clone --quiet "$JETGPIO_URL" "$tmp/JETGPIO"
  git -C "$tmp/JETGPIO" -c advice.detachedHead=false checkout --quiet "$JETGPIO_COMMIT"
  make -C "$tmp/JETGPIO"
  make -C "$tmp/JETGPIO" install
  rm -rf "$tmp"
fi

# --------------------------------------------------------------------------- 4
log "4/7 Building radar setup, DCA1000 tools and trigger programs"
as_user cmake -S "$REPO_DIR/setup_radar" -B "$REPO_DIR/setup_radar/build"
as_user cmake --build "$REPO_DIR/setup_radar/build" -j"$(nproc)"
as_user mkdir -p "$REPO_DIR/DCA1000/SourceCode/Release"
as_user make -C "$REPO_DIR/DCA1000/SourceCode"
as_user make -C "$REPO_DIR/src/hardware_trigger_mqtt"
as_user make -C "$REPO_DIR/src/hardware_trigger"
echo "$REPO_DIR/DCA1000/SourceCode/Release" > /etc/ld.so.conf.d/netradar-dca1000.conf
ldconfig

# --------------------------------------------------------------------------- 5
log "5/7 Creating Python environment (.venv) — this can take a while"
[[ -d "$REPO_DIR/.venv" ]] || as_user python3 -m venv "$REPO_DIR/.venv"
as_user "$REPO_DIR/.venv/bin/pip" install --upgrade pip
as_user "$REPO_DIR/.venv/bin/pip" install -r "$REPO_DIR/requirements.txt"
# CPU build of PyTorch: on aarch64 PyPI ships the CPU wheel (see README).
as_user "$REPO_DIR/.venv/bin/pip" install "torch==$TORCH_VERSION"

# --------------------------------------------------------------------------- 6
if ((SKIP_NETWORK == 0)); then
  log "6/7 Network: DCA1000 link on $DCA_IFACE ($DCA_HOST_CIDR) + socket buffers"
  if nmcli -t -f NAME connection show | grep -qx "$DCA_CONNECTION"; then
    echo "  connection '$DCA_CONNECTION' already exists — leaving it as is"
  elif ip link show "$DCA_IFACE" >/dev/null 2>&1; then
    nmcli connection add type ethernet ifname "$DCA_IFACE" con-name "$DCA_CONNECTION" \
      ipv4.method manual ipv4.addresses "$DCA_HOST_CIDR" ipv4.never-default yes \
      ipv6.method disabled connection.autoconnect yes >/dev/null
    nmcli connection up "$DCA_CONNECTION" >/dev/null 2>&1 \
      || warn "could not bring up $DCA_CONNECTION now (is the DCA1000 cable connected?)"
  else
    warn "interface '$DCA_IFACE' not found. Available: $(ip -br link | awk '{print $1}' | tr '\n' ' ')"
    warn "re-run with --dca-iface <name> for the port wired to the DCA1000"
  fi
  printf 'net.core.rmem_max=16777216\nnet.core.wmem_max=16777216\n' > /etc/sysctl.d/90-netradar.conf
  sysctl -q --system
else
  log "6/7 Skipping network setup (--skip-network)"
fi

# --------------------------------------------------------------------------- 7
as_user mkdir -p "$REPO_DIR/logs"
if ((INSTALL_SERVICE == 1)); then
  log "7/7 Installing systemd service 'netradar-node'"
  sed -e "s|@REPO_DIR@|$REPO_DIR|g" -e "s|@USER_HOME@|$USER_HOME|g" \
    "$REPO_DIR/scripts/netradar-node.service.in" > /etc/systemd/system/netradar-node.service
  systemctl daemon-reload
  systemctl enable netradar-node >/dev/null
  echo "  enabled; it starts once ./configure.sh has written .env"
else
  log "7/7 Skipping systemd service (--no-service)"
fi

cat <<EOF

NetRadar node installed in $REPO_DIR.

Next steps:
  1. ./configure.sh            (paste the block for this node from the fusion center)
  2. sudo ./time-sync/chrony-setup.sh slave <fusion-center-host>
  3. Reboot once so the GPIO library and drivers are loaded cleanly.

Docs: https://netradar-testbed.github.io
EOF
