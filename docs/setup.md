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

1. Fill `POSTGRES_PASSWORD`, `INGESTOR_DB_PASSWORD`, `GRAFANA_RO_DB_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`, each with the output of:
   ```bash
   python -c "import secrets; print(secrets.token_hex(24))"
   ```
2. Generate `TIMETAGGER_CREDENTIALS` locally (your password never leaves the machine) and paste the printed line into `.env`:
   ```bash
   uv run --with bcrypt python scripts/hash_password.py
   ```
3. Leave `TIMETAGGER_TOKEN` empty for now.

## 3. Start the stack

```bash
docker compose up -d --build
docker compose ps
```

All services should be `running`; `postgres` and `ingestor` should become `healthy`.
Until the token is set, `docker compose logs ingestor` shows authentication errors — expected.

Only HTTPS (port 443) is exposed: always type `https://` in front of the addresses below.

## 4. Name resolution

The sites are `tt.lifelog.lan`, `dash.lifelog.lan`, `sync.lifelog.lan`.

- **Router (needed for the iPhone):** add local DNS entries for the three names pointing at the server's LAN IP. If the router cannot do this, a local DNS server (AdGuard Home) is needed — raise it before continuing.
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

- TimeTagger: `https://tt.lifelog.lan` → log in. On the iPhone, open it in Safari → Share → Add to Home Screen.
- Grafana: `https://dash.lifelog.lan` → log in as `admin` with `GRAFANA_ADMIN_PASSWORD` → Lifelog → This Week.
- Sync button: the "Sync now" link at the top of the dashboard (or `https://sync.lifelog.lan/sync`).

## Router checklist (security)

- No port forwarding to the server; UPnP disabled.
- DHCP reservations for the server, iPhone and laptop.
- IoT devices, TVs and guests on the guest Wi-Fi.
