# Setup

## 1. Server prerequisites (Fedora)

```bash
sudo dnf -y install dnf-plugins-core
sudo dnf config-manager addrepo --from-repofile=https://download.docker.com/linux/fedora/docker-ce.repo
sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"   # log out and back in afterwards
```

On Windows/macOS for development: Docker Desktop, Colima, Rancher Desktop or Podman Desktop.

`uv` is used for the tests and the password script. If `uv` is not on your PATH (for example after `pip install --user uv` on Windows), run it as `python -m uv` instead.

## 2. Configure `.env`

```bash
cp .env.example .env
```

1. Fill `POSTGRES_PASSWORD`, `INGESTOR_DB_PASSWORD`, `GRAFANA_RO_DB_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`, `GRAFANA_SECRET_KEY`, each with the output of:
   ```bash
   python -c "import secrets; print(secrets.token_hex(24))"
   ```
2. Generate `TIMETAGGER_CREDENTIALS` locally (your password never leaves the machine) and paste the printed line into `.env`:
   ```bash
   uv run --with bcrypt python scripts/hash_password.py
   ```
3. Leave `TIMETAGGER_TOKEN` empty for now.
4. On a laptop, set `CADDY_BIND=127.0.0.1` so the stack is only reachable from the laptop itself. Keep `0.0.0.0` on the home server.
5. Choose a login for the goals page and the Sync button: set `SYNC_BASIC_AUTH_USER` (for example your first name), then hash a password and paste it as `SYNC_BASIC_AUTH_HASH='…'` (keep the single quotes):
   ```bash
   docker run --rm -it caddy:2 caddy hash-password
   ```
   Save that password in your password manager: your browser asks for it once.

On the server, protect the file: `chmod 600 .env`.

## 3. Start the stack

```bash
docker compose up -d --build
docker compose ps
```

All services should be `running`; `postgres` and `ingestor` should become `healthy`.
Until the token is set, `docker compose logs ingestor` shows authentication errors — expected.

Only HTTPS (port 443) is exposed: always type `https://` in front of the addresses below.

### Passwords are applied on first start only

Postgres role passwords and the Grafana admin password are taken from `.env` only when their volumes are empty (the very first `docker compose up`). Editing `.env` later does **not** change them — the ingestor or Grafana login would then fail.

- Change a database password: `docker compose exec postgres psql -U postgres -d lifelog -c "ALTER ROLE ingestor PASSWORD '<new>'"` (same for `grafana_ro`), then update `.env` and run `docker compose up -d`.
- Change the Grafana admin password: `docker compose exec grafana grafana cli admin reset-admin-password '<new>'`.
- Start over completely: `docker compose down -v` — ⚠️ this **deletes all data** (TimeTagger records, database, dashboards state, Caddy's certificate authority). Only for a fresh setup.

**Updating an existing install:** new required `.env` values (such as `SYNC_BASIC_AUTH_USER` / `SYNC_BASIC_AUTH_HASH`) must be added before `docker compose up -d --build`, otherwise compose stops with "set in .env".

## 4. Name resolution

The sites are `tt.lifelog.lan`, `dash.lifelog.lan`, `sync.lifelog.lan`.

- **Router (needed for the iPhone):** add local DNS entries for the three names pointing at the server's LAN IP. If the router cannot do this, a local DNS server (AdGuard Home) is needed — raise it before continuing. If Safari on the iPhone cannot open `*.lan` addresses, turn off 'Limit IP Address Tracking' for the home Wi-Fi (Settings → Wi-Fi → (i)) and make sure iCloud Private Relay is off for that network.
- **Laptop (quick alternative for testing):** add to the hosts file (`C:\Windows\System32\drivers\etc\hosts` on Windows, `/etc/hosts` elsewhere):
  ```
  <server-ip>  tt.lifelog.lan dash.lifelog.lan sync.lifelog.lan
  ```
  Use `127.0.0.1` when running the stack on the laptop itself.

## 5. Trust Caddy's local certificate authority

Export the root certificate (run on the machine running the stack):

```bash
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt
```

- **Windows:** double-click `caddy-root.crt` → Install Certificate → Local Machine → "Place all certificates in the following store" → Trusted Root Certification Authorities.
- **macOS:** open it in Keychain Access → System keychain → set "When using this certificate" to Always Trust.
- **iPhone:**
  1. AirDrop or email `caddy-root.crt` to the phone and open it → "Profile Downloaded".
  2. Settings → General → VPN & Device Management → install the profile.
  3. Settings → General → About → Certificate Trust Settings → enable full trust for the Caddy root.

**What trusting this certificate means:** a device that trusts Caddy's root certificate will accept *any* website certificate signed with its key. Anyone who gets that key (it lives in the `caddy_data` volume and in backups) could impersonate websites to your devices. So:
- Trust only the root certificate from your home server on your phone.
- Each stack creates its own certificate authority: remove a laptop test certificate from your devices when you are done testing.
- Keep the server and its backups private.

Firefox uses its own certificate store: Settings → Privacy & Security → Certificates → View Certificates → Authorities → Import.

Never tap "continue anyway" on a certificate warning for these sites after this — a warning means something is wrong.

## 6. iPhone network setting

Settings → Wi-Fi → (i) next to the home network → Private Wi-Fi Address → **Fixed**. Then reserve that IP in the router's DHCP settings.

## 7. TimeTagger API token

```bash
docker compose exec -it ingestor lifelog get-token --username <your-username>
```

Paste the printed token into `.env` as `TIMETAGGER_TOKEN`, then recreate the ingestor so it picks up the new value:

```bash
docker compose up -d ingestor
docker compose exec ingestor lifelog sync
```

Expected: `success: N records`.

## 8. Use it

- TimeTagger: `https://tt.lifelog.lan/timetagger/app/` → log in. On the iPhone, open that address in Safari → Share → Add to Home Screen.
- Grafana: `https://dash.lifelog.lan` → log in as `admin` with `GRAFANA_ADMIN_PASSWORD` → Lifelog → This Week.
- Sync button: the "Sync now" link at the top of the dashboard (or `https://sync.lifelog.lan/sync`).
- Weekly goals: `https://sync.lifelog.lan/goals` (or the **Set goals** link on the dashboard). Set them each Monday; last week's values are offered as a suggestion.
- Study counts entries tagged `#study`, `#ds` / `#ds/<topic>` or `#systemanalysis`, once per entry.
- Always include at least one `#tag` in what you log (e.g. `#study`). Entries without tags appear as `(untagged)`.

## Router checklist (security)

- No port forwarding to the server; UPnP disabled.
- DHCP reservations for the server, iPhone and laptop.
- IoT devices, TVs and guests on the guest Wi-Fi.
