# Accessing Your Private Hunting Job System

This is **private** — only you. No public repo, no third-party trust. You run
everything on your own Oracle Cloud VM and reach it over SSH.

## 1. SSH tunnel (recommended — keeps the dashboard off the public internet)

The dashboard binds to `127.0.0.1` on the server, so it is **not** exposed to
the internet. You reach it through a secure SSH tunnel from your laptop:

```bash
# On your laptop — forward local port 8599 to the server's dashboard
ssh -N -L 8599:127.0.0.1:8599 root@<SERVER_IP>
```

Then open **http://localhost:8599** in your browser. It's your VM, tunneled
through SSH. No open ports, no exposure.

## 2. If you want direct internet access (less private)

Edit the `streamlit.service` file on the server to bind `0.0.0.0` instead of
`127.0.0.1`, then open port 8599 in the Oracle Cloud security list:

```bash
# On the server
sed -i 's/127.0.0.1/0.0.0.0/' /etc/systemd/system/streamlit.service
systemctl daemon-reload
systemctl restart streamlit.service
```

> ⚠️ This exposes the dashboard to the internet. Since it only *reads* your
> career data and never sends secrets, it's low risk — but add a basic auth
> proxy if you want it truly locked down.

## 3. Managing services

```bash
# Dashboard
systemctl status streamlit.service
journalctl -u streamlit.service -f          # live logs

# Scheduler (daily hunt)
systemctl status scheduler.timer
journalctl -u scheduler.timer -f

# Run a hunt cycle manually right now
sudo -u hunting bash -lc "cd /opt/hunting-job-system && uv run python scheduler.py --once"
```

## 4. Keeping it up to date

```bash
# On the server
cd /opt/hunting-job-system
git pull --ff-only            # grab your latest code
uv pip install -e .           # reinstall deps if pyproject changed
systemctl restart streamlit   # reload the dashboard
```

## 5. Your API keys

The `.env` file is **git-ignored** and never leaves your control. Copy it in
once after setup:

```bash
# On your laptop
scp .env root@<SERVER_IP>:/root/.env
# Then move it into the app dir on the server
ssh root@<SERVER_IP> "mkdir -p /opt/hunting-job-system && cp /root/.env /opt/hunting-job-system/.env"
```

The app reads `GOOGLE_API_KEY` (and `XAI_API_KEY` if you add one) from the
environment. Add a small loader to the systemd service if you prefer `.env`
over raw env vars — see `deploy/README.md` for the snippet.

## 6. Costs

Oracle Cloud **always-free** ARM VM (Ampere A1, 1/8 OCPU, 1 GB RAM) is free
forever. This project is light enough to run on that. If you ever need more
memory for the vector store, the free tier is still plenty for a handful of
applications.
