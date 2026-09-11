# Full Deploy Guide — Hunting Job System on Oracle Cloud (Private)

A complete walkthrough from a blank VM to a running, private system.

---

## Step 1 — Create the Oracle Cloud Always Free VM

1. Log in to the Oracle Cloud Console (create a free account — no charge card
   required for the Always Free tier).
2. Go to **Compute → Create Instance**.
3. Choose:
   - **Image:** Ubuntu 22.04 LTS (or Oracle Linux — either works).
   - **Shape:** *Ampere A1 Compute* (the Always Free ARM shape: 1/8 OCPU,
     1 GB RAM). Select **Always Free** in the shape dropdown.
4. Add an SSH key (generate one on your laptop if you don't have one):
   ```bash
   ssh-keygen -t rsa -b 4096 -f ~/.oracle_cloud -N ""
   ```
   Paste the **public** key (`cat ~/.oracle_cloud.pub`) into the form.
5. In **Networking**, create a subnet with a **public IP** and open port **22**
   (SSH). Leave 8599 closed for now.
6. Create the instance. Note the **public IP** — call it `<SERVER_IP>`.

---

## Step 2 — Connect to the VM

```bash
ssh -i ~/.oracle_cloud root@<SERVER_IP>
```

---

## Step 3 — Prepare your code

You have two choices. Pick one:

### Option A — Private git repo (recommended for ongoing updates)

Push your project to a private GitHub/GitLab repo. Then on the VM, generate a
deploy key:

```bash
ssh-keygen -t ed25519 -f ~/.deploy_key -N ""
```

Add the **public** key (`cat ~/.deploy_key.pub`) to your repo's **Deploy
Keys** (read-only, can pull). Then edit `setup.sh` and replace `<GIT_URL>`:

```bash
sed -i "s|<GIT_URL>|git@github.com:your-user/hunting-job-system.git|" deploy/setup.sh
```

### Option B — Plain copy (no git)

Skip the git step. After running `setup.sh`, copy your project in:

```bash
scp -r /path/to/hunting-job-system root@<SERVER_IP>:/opt/hunting-job-system/
```

---

## Step 4 — Run the setup script

Copy `setup.sh` to the VM (Option B only; Option A clones it):

```bash
scp deploy/setup.sh root@<SERVER_IP>:/root/setup.sh
```

Then run it:

```bash
bash setup.sh
```

It will:
- Install Python 3.11 + build tools
- Install `uv`
- Deploy the app to `/opt/hunting-job-system`
- Create a venv and install dependencies
- Install the **scheduler** (daily 7am systemd timer)
- Install the **Streamlit dashboard** (systemd service, bound to localhost)

---

## Step 5 — Add your API keys

The app reads `GOOGLE_API_KEY` (and `XAI_API_KEY` if you add one) from the
environment, loaded via systemd `EnvironmentFile` from `/opt/hunting-job-system/.env`.

On your laptop:

```bash
# Copy your local .env to the server
scp .env root@<SERVER_IP>:/root/.env
ssh root@<SERVER_IP> "mkdir -p /opt/hunting-job-system && cp /root/.env /opt/hunting-job-system/.env && chmod 600 /opt/hunting-job-system/.env"
```

Then reload and restart:

```bash
ssh root@<SERVER_IP> "systemctl daemon-reload && systemctl restart streamlit scheduler.timer"
```

---

## Step 6 — Access the dashboard

The dashboard is bound to `127.0.0.1` on the server, so it is **not** exposed
to the internet. From your laptop, open an SSH tunnel:

```bash
ssh -N -L 8599:127.0.0.1:8599 root@<SERVER_IP>
```

Then open **http://localhost:8599** in your browser.

---

## Step 7 — Verify everything is running

```bash
ssh root@<SERVER_IP>
systemctl status streamlit.service        # dashboard
systemctl status scheduler.timer          # daily hunt
journalctl -u streamlit.service -f        # live dashboard logs
journalctl -u scheduler.timer -f          # daily run logs
```

Run a hunt cycle manually to confirm the pipeline works:

```bash
sudo -u hunting bash -lc "cd /opt/hunting-job-system && uv run python scheduler.py --once"
```

---

## Step 8 — Keep it updated

```bash
ssh root@<SERVER_IP>
cd /opt/hunting-job-system
git pull --ff-only          # Option A: grab latest code
uv pip install -e .         # only if pyproject.toml changed
systemctl restart streamlit # reload the dashboard
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Dashboard won't load on `:8599` | Check tunnel is running; `journalctl -u streamlit.service` |
| `GOOGLE_API_KEY` not found | Confirm `/opt/hunting-job-system/.env` exists and has the key |
| Scheduler didn't run | `journalctl -u scheduler.timer -f`; check `OnCalendar` |
| Out of memory (vector store) | The free tier (1 GB) is tight; reduce `top_k` in the generator |

---

## Cost

**$0 forever.** The Oracle Cloud Always Free ARM VM covers this project. The
Gemini free tier covers the LLM/embeddings. No paid services anywhere.
