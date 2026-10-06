# Home server deployment

The home server is the primary backend. Render + Supabase stay up as a standby
that takes over automatically whenever the home server is offline.

```
              Vercel frontend
        tries home, falls back to Render
           │                       │
  api.<domain>/spotify       Render backend
   (tunnel → Caddy)                │
           │                       │
  home backend ── shared Postgres  Supabase
                 (one instance,
                  db per app)
```

- **Both sides record plays.** The home backend polls Spotify itself
  (`ENABLE_LOCAL_SYNC=true`), and cron-job.org keeps calling Render's
  `/api/cron/sync-tracks` as it does today. So plays get recorded even while
  the home server is down.
- **Hourly + at boot** (`hourly.sh`): the home server copies plays that only
  the standby recorded, then rebuilds the related-tracks rollup.
- **Nightly** (`push-to-supabase.sh`): Supabase gets replaced with an exact
  copy of the home database, so the standby has the full history.
- **Failover** (`frontend/app/lib/api.ts`): the frontend fails over when the
  tunnel reports the home server offline (Cloudflare 530/52x), or when a
  request errors or hangs for 10s. It then skips the home server for 30s.

All database traffic goes through Supabase's session pooler, so IPv6 isn't
needed.

**One tunnel and one proxy for the whole machine.** The host's `cloudflared`
service sends all of `api.andrewdurnford.com` to Caddy on `localhost:8080`
(`deploy/home/caddy/`), which routes by path prefix: `/spotify/*` goes to this
app with the prefix stripped. Apps join the `proxy` Docker network under a
unique alias (`spotify-api` here). Adding an app never touches the tunnel.

**One Postgres for the whole machine.** `deploy/home/postgres/` runs a single
instance that every app on the laptop shares. Each app gets its own database
and login role, and reaches the instance as host `postgres` on the `pg` Docker
network. This app doesn't run a Postgres of its own.

## One-time setup

### 1. Laptop

```bash
# Keep running with the lid closed
sudo sed -i 's/^#\?HandleLidSwitch=.*/HandleLidSwitch=ignore/; s/^#\?HandleLidSwitchExternalPower=.*/HandleLidSwitchExternalPower=ignore/' /etc/systemd/logind.conf
sudo systemctl restart systemd-logind

# Start Docker on boot (containers restart themselves: restart: unless-stopped)
sudo systemctl enable docker

git clone <repo> ~/spotify-analytics
```

### 2. Cloudflare Tunnel (once per machine)

Your domain's DNS has to be on Cloudflare. The free plan works: move the
nameservers there and recreate the Vercel records.

Zero Trust → Networks → Tunnels → Create tunnel (Cloudflared), and install it
on the host as a service (`cloudflared service install <token>`). Then add one
published application route, and no others for this hostname:

- Hostname: `api.andrewdurnford.com`, path blank
- Service: `HTTP` → `localhost:8080`

### 3. Shared Caddy proxy (once per machine)

Skip this if another app already set it up.

```bash
cp -r ~/spotify-analytics/deploy/home/caddy ~/caddy
cd ~/caddy && docker compose up -d
```

`~/caddy/Caddyfile` is now the machine's routing table; edit that copy, not
the repo's. Until the app is up, `/spotify/*` returns 502.

### 4. Shared Postgres (once per machine)

Skip this if another app already set it up.

```bash
cp -r ~/spotify-analytics/deploy/home/postgres ~/postgres
cd ~/postgres
echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)" > .env   # superuser, admin only
docker compose up -d
```

Keep the shared instance on Postgres 17 or newer. Supabase runs 17, and
restoring its dumps into an older server can fail.

### 5. This app's database and role

Replace `<password>` with a fresh one from `openssl rand -hex 24`:

```bash
docker exec -it postgres psql -U postgres \
  -c "create role spotify login password '<password>'" \
  -c "create database spotify owner spotify"
```

Other apps follow the same pattern with their own role and database. The
`spotify` role owns only its own database, so apps can't read each other's
data.

### 6. Configure and start

```bash
cd ~/spotify-analytics
cp deploy/home/env.home.example .env.home   # fill it in; DATABASE_URL uses the password above
deploy/home/pull-from-supabase.sh           # copy Supabase -> home, once
docker compose -f docker-compose.home.yml --env-file .env.home up -d --build
```

Check it's reachable:

```bash
curl http://localhost:8080/spotify/health
curl https://api.andrewdurnford.com/spotify/health
```

### 7. Cron jobs

Run `crontab -e` and add:

```
@reboot    sleep 120 && ~/spotify-analytics/deploy/home/hourly.sh >> ~/spotify-home.log 2>&1
15 * * * * ~/spotify-analytics/deploy/home/hourly.sh >> ~/spotify-home.log 2>&1
30 9 * * * ~/spotify-analytics/deploy/home/push-to-supabase.sh >> ~/spotify-home.log 2>&1
```

Cron uses the laptop's timezone. `30 9` is 09:30 UTC (about 4–5am Eastern) on a
UTC machine. While the push runs (about a minute), Render's requests stall, so
schedule it when nobody's browsing.

### 8. Vercel

Set these env vars, then redeploy. The `NEXT_PUBLIC_*` values get baked in at
build time.

| Variable | Value |
| --- | --- |
| `API_URL`, `NEXT_PUBLIC_API_URL` | `https://api.andrewdurnford.com/spotify` |
| `API_FALLBACK_URL`, `NEXT_PUBLIC_API_FALLBACK_URL` | the Render backend URL |

### 9. Render / Supabase / cron-job.org

No changes. Keep both cron-job.org jobs pointed at Render.

## Day to day

- **Deploys are automatic.** When CI passes on `main`, the `deploy-home` job
  runs `deploy.sh` on this machine's self-hosted runner: build, swap, migrate,
  health check, and roll back to the previous commit if the new one isn't
  healthy. The swap takes a few seconds; the frontend fails over to Render
  meanwhile. Same thing by hand:
  ```bash
  deploy/home/deploy.sh            # origin/main, or pass a commit
  ```
  Rollbacks don't undo migrations, so keep them compatible with the previous
  release.
- **Migrations** go to both databases, or the nightly push fails on schema
  mismatch. `deploy.sh` migrates home; Render migrates Supabase as before.
- **Runner:** `~/actions-runner/spotify-analytics`, run as a user systemd
  service (lingering enabled so it starts at boot without a login):
  ```bash
  systemctl --user status actions-runner-spotify-analytics
  journalctl --user -u actions-runner-spotify-analytics -f
  ```
  Setup, once per repo: download the runner, `./config.sh --url
  <repo> --token <token> --labels ad-server-2005`, then a user unit running
  `run.sh` and `loginctl enable-linger`.
- **After an outage longer than 14 days:** run the merge by hand with a longer
  window:
  ```bash
  docker compose -f docker-compose.home.yml --env-file .env.home exec backend python -m app.scripts.merge_from_backup --lookback-days 60 --commit
  ```
- **Push refused** ("Home has fewer streams than Supabase"): the home database
  lost data. Don't force it. Restore it from Supabase instead. Only this app's
  database is touched; other apps are fine:
  ```bash
  docker exec postgres psql -U postgres -c "drop database spotify with (force)" -c "create database spotify owner spotify"
  deploy/home/pull-from-supabase.sh
  ```
- **The shared instance is a single point of failure for every app.** If it
  goes down, they all do. For this app, the Supabase copy is the backup. Other
  apps need their own backups (e.g. a nightly `pg_dumpall`).
- **Logs:**
  ```bash
  docker compose -f docker-compose.home.yml --env-file .env.home logs -f backend
  ```
  Cron output goes to `~/spotify-home.log`.
