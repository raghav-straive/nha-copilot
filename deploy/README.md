# Deploying the NHA Co-pilot

**Architecture:** frontend on **GitHub Pages** (HTTPS) → backend on a **GCP Compute
Engine VM** behind **Caddy** (auto-HTTPS via a `nip.io` hostname) → BigQuery.

The VM uses its **attached service account** for BigQuery (no key file on disk).

---

## Part A — Backend on a GCP VM

### 1. Create the VM (Cloud Console → Compute Engine → VM instances → Create)
- Machine type: **e2-small** (enough for a prototype).
- Region: **asia-south1** (same as your data).
- Boot disk: **Debian 12**.
- **Identity and API access → Service account:** create/use one with
  **BigQuery Data Viewer** + **BigQuery Job User** (read-only) and set
  **Access scopes → Allow full access to all Cloud APIs** (or add the BigQuery scope).
- **Firewall:** check **Allow HTTP** and **Allow HTTPS**.
- Create it, then note the **External IP** (e.g. `34.93.1.2`).

### 2. SSH into the VM (the "SSH" button in the console) and install deps
```bash
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip git debian-keyring debian-archive-keyring apt-transport-https curl
# Caddy (auto-HTTPS reverse proxy)
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy
```

### 3. Get the code and install the backend
```bash
sudo useradd -r -m -d /opt/nha-copilot nha || true
sudo git clone https://github.com/raghav-straive/nha-copilot.git /opt/nha-copilot
cd /opt/nha-copilot/backend
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
sudo chown -R nha:nha /opt/nha-copilot
```

### 4. Configure secrets
```bash
sudo mkdir -p /etc/nha-copilot
sudo cp /opt/nha-copilot/deploy/env.example /etc/nha-copilot/env
sudo nano /etc/nha-copilot/env      # set OPENAI_API_KEY, JWT_SECRET, APP_USERS
sudo chown nha:nha /etc/nha-copilot/env && sudo chmod 600 /etc/nha-copilot/env
```
Generate a JWT secret: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`

### 5. Run the backend as a service
```bash
sudo cp /opt/nha-copilot/deploy/nha-copilot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nha-copilot
sudo systemctl status nha-copilot          # should be active (running)
curl -s http://127.0.0.1:8000/health       # {"status":"ok"}
```

### 6. Put Caddy in front (auto-HTTPS)
Pick a `nip.io` hostname from the External IP — dashes for dots, e.g. IP
`34.93.1.2` → **`34-93-1-2.nip.io`**.
```bash
echo "SITE_ADDRESS=34-93-1-2.nip.io" | sudo tee /etc/caddy/Caddyfile.env
sudo cp /opt/nha-copilot/deploy/Caddyfile /etc/caddy/Caddyfile
# load the env var into caddy
sudo sed -i 's|^EnvironmentFile=.*||' /lib/systemd/system/caddy.service 2>/dev/null || true
sudo systemctl edit caddy --force --full   # add: EnvironmentFile=/etc/caddy/Caddyfile.env  under [Service]
sudo systemctl restart caddy
```
Test from your laptop: `https://34-93-1-2.nip.io/health` → `{"status":"ok"}` with a valid padlock. **This HTTPS URL is your backend base.**

---

## Part B — Frontend on GitHub Pages

1. In the repo: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
2. **Settings → Secrets and variables → Actions → Variables → New variable:**
   `VITE_API_BASE = https://34-93-1-2.nip.io`
3. Run the **Deploy frontend to GitHub Pages** workflow (Actions → Run workflow).
   It builds with that API base and publishes to
   **https://raghav-straive.github.io/nha-copilot/**.

Make sure the backend's `CORS_ORIGINS` in `/etc/nha-copilot/env` is
`https://raghav-straive.github.io`, then `sudo systemctl restart nha-copilot`.

> **Pages will not work until it is available for this repository.** It is
> unavailable for a *private* repo on a Free plan, which is why the workflow's
> automatic `push` trigger is switched off — it only ever failed at the deploy
> step. Either make the repo public, or upgrade the plan, or skip Pages entirely
> and serve the frontend from the same nginx host as the backend (Layout A in
> `nginx.conf.example`, which is the recommended setup and also lets the
> httpOnly auth cookie work). See "Deploying this copy" in the root README.

---

## Verify end-to-end
Open the frontend — **https://raghav-straive.github.io/nha-copilot/** on a Pages
deployment, or just the nginx host itself with Layout A — log in with an
`APP_USERS` account, and run a query. If login hangs, check: backend HTTPS reachable, CORS
origin matches exactly, and `VITE_API_BASE` has no trailing slash.

## Updating later
- **Frontend:** push to `main` → the workflow redeploys Pages automatically.
- **Backend:** `cd /opt/nha-copilot && sudo git pull && sudo systemctl restart nha-copilot`
  (re-run pip install if requirements changed).
