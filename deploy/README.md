# Deploy — Private, on your own Oracle Cloud VM

This is a **private** deployment. No public repo, no third-party cloud CI, no
shared secrets. Everything runs on your own Oracle Cloud always-free ARM VM.

## Files

| File | Purpose |
|---|---|
| `setup.sh` | One-command server setup (Python, uv, venv, systemd services) |
| `ACCESS.md` | How to reach the dashboard (SSH tunnel) and manage services |
| `deploy-guide.md` | Full step-by-step from a blank VM to a running system |

## Quick start

1. Spin up an Oracle Cloud **Always Free** ARM VM (Ampere A1, 1/8 OCPU, 1 GB).
2. SSH in as root.
3. Edit `setup.sh` — replace `<GIT_URL>` with your repo URL (see below).
4. Run: `bash setup.sh`
5. Copy your `.env` into `/opt/hunting-job-system/.env` (git-ignored).
6. SSH-tunnel to `http://localhost:8599` on your laptop.

See `deploy-guide.md` for the full walkthrough.

## About the repo

Because this is private, you still need a git repo to deploy from. Two options:

- **Private GitHub/GitLab repo** — the VM clones it with an SSH deploy key.
  The `.env` is git-ignored so keys never enter version control.
- **Local copy** — `scp -r` the whole project into `/opt/hunting-job-system`
  on the server. No git needed at all.

The `setup.sh` handles both: if the repo already exists it does `git pull`,
otherwise it clones. If you prefer a plain copy, skip the git step and just
`scp` your files in.

## Security notes

- The dashboard binds to `127.0.0.1` on the server — **not** exposed to the
  internet. Reach it via SSH tunnel (see `ACCESS.md`).
- `.env` is git-ignored and loaded via systemd `EnvironmentFile`.
- No credentials are hardcoded anywhere in this deploy package.
