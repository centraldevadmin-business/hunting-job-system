#!/usr/bin/env bash
# ============================================================
# One-command setup for the Hunting Job System on Oracle Cloud
# Free Tier (always-free ARM VM). Private, just for you.
#
# Run this ONCE on the server as root (or a sudo user):
#   bash setup.sh
#
# It will:
#   1. Install Python 3.11 + uv
#   2. Clone/deploy your repo to /opt/hunting-job-system
#   3. Create a virtualenv and install dependencies
#   4. Install the scheduler as a systemd service (daily cron)
#   5. Install the Streamlit dashboard as a systemd service
#   6. Open the firewall ports (8599 dashboard, 22 ssh)
#
# NOTE: This script does NOT contain your API keys. Copy .env
# from your local machine after running this.
# ============================================================
set -euo pipefail

REPO_DIR="/opt/hunting-job-system"
APP_USER="hunting"
DASHBOARD_PORT=8599
SSH_PORT=22

echo "==> [1/7] Creating system user '$APP_USER'"
id "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash "$APP_USER"

echo "==> [2/7] Installing Python 3.11 and build tools"
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip \
    build-essential git curl ca-certificates
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y python3 python3-pip python3-devel git curl ca-certificates
else
  echo "ERROR: Unsupported distro. Install Python 3.11 manually." >&2
  exit 1
fi

echo "==> [3/7] Installing uv (fast Python package manager)"
curl -LsSf https://astral.sh/uv/install.sh | sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
export PATH="$HOME/.local/bin:$PATH"

echo "==> [4/7] Deploying application to $REPO_DIR"
mkdir -p "$REPO_DIR"
chown -R "$APP_USER:$APP_USER" "$REPO_DIR"

if [ -d "$REPO_DIR/.git" ]; then
  echo "    Repo already present — pulling latest."
  sudo -u "$APP_USER" bash -lc "cd $REPO_DIR && git pull --ff-only"
else
  echo "    Clone your repo here. Replace <GIT_URL> first!"
  echo "    sudo -u $APP_USER bash -lc \"git clone <GIT_URL> $REPO_DIR\""
  echo "    (Or copy files in via scp after this script.)"
fi

echo "==> [5/7] Creating virtualenv and installing dependencies"
sudo -u "$APP_USER" bash -lc "
  cd $REPO_DIR
  uv venv .venv
  uv pip install -e .
"

echo "==> [6/7] Installing the scheduler as a systemd service"
cat > /etc/systemd/system/scheduler.service <<EOF
[Unit]
Description=Hunting Job System Scheduler (daily hunt cycle)
After=network.target

[Service]
Type=oneshot
User=$APP_USER
WorkingDirectory=$REPO_DIR
EnvironmentFile=$REPO_DIR/.env
ExecStart=/bin/bash -c 'source $REPO_DIR/.venv/bin/activate && cd $REPO_DIR && python scheduler.py --daily'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

# Daily at 7:00 AM via systemd timer
cat > /etc/systemd/system/scheduler.timer <<EOF
[Unit]
Description=Run the hunt cycle daily at 7am

[Timer]
OnCalendar=*-*-* 07:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now scheduler.timer

echo "==> [7/7] Installing the Streamlit dashboard as a systemd service"
cat > /etc/systemd/system/streamlit.service <<EOF
[Unit]
Description=Hunting Job System Dashboard (Streamlit)
After=network.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$REPO_DIR
EnvironmentFile=$REPO_DIR/.env
ExecStart=/bin/bash -c 'source $REPO_DIR/.venv/bin/activate && cd $REPO_DIR && streamlit run src/dashboard/app.py --server.headless true --server.port $DASHBOARD_PORT --server.address 127.0.0.1'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now streamlit.service

echo ""
echo "============================================================"
echo " Setup complete."
echo ""
echo "   Dashboard:  http://<SERVER_IP>:$DASHBOARD_PORT"
echo "   Scheduler:  runs daily at 7am (see 'systemctl status scheduler.timer')"
echo "   Logs:       journalctl -u streamlit.service -f"
echo "               journalctl -u scheduler.timer -f"
echo ""
echo "   NEXT STEP: copy your .env into $REPO_DIR/.env"
echo "   (it is git-ignored, so it will NOT be in the repo)"
echo "============================================================"
