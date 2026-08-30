# BeanNote

Personlig kaffe-journal og rating-PWA. Scan poser, gem smagninger, og kør den som ét Docker-image på Unraid.

**Version:** 1.0.2  
**Image:** `ghcr.io/stefan240987/beannote:1.0.0` eller `:latest`  
**Port:** `8502`  
**Data:** `/app/data` (SQLite, brugerfotos, katalog-uploads)

---

## Installer på Unraid

### 1. Forudsætninger

- Unraid 6.12+ med Docker
- Et JWT-secret: `openssl rand -hex 32`
- Appdata på **cache-only** share (ikke array/FUSE). Anbefalet sti:  
  `/mnt/cache/appdata/beannote/data`
- Valgfrit: [Gemini API-nøgle](https://aistudio.google.com/apikey) til posescanning
- Valgfrit men anbefalet: SWAG (eller anden reverse proxy) med HTTPS

### 2. Tilføj templaten

1. Hent [`unraid/beannote.xml`](unraid/beannote.xml) fra dette repo.
2. Gem den på Unraid-flash som:

   `/boot/config/plugins/dockerMan/templates-user/beannote.xml`

3. **Docker** → **Add Container** → vælg **beannote** i Template-dropdown.

Alternativt: **Apps** → ⚙️ → **Template Repositories** → tilføj  
`https://github.com/stefan240987/beannote`

### 3. Udfyld de påkrævede felter

| Felt | Variabel | Værdi |
|---|---|---|
| Appdata | — | Host: `/mnt/cache/appdata/beannote/data` → Container: `/app/data` |
| WebUI | — | `8502` |
| Environment | `ENVIRONMENT` | `production` |
| Timezone | `TZ` | `Europe/Copenhagen` |
| PUID / PGID | `PUID` / `PGID` | `99` / `100` |
| JWT Secret | `JWT_SECRET` | output fra `openssl rand -hex 32` |
| Admin Email | `ADMIN_EMAIL` | din e-mail |
| Admin Password | `ADMIN_PASSWORD` | mindst 8 tegn |
| Reset DB on start | `RESET_DB_ON_START` | `false` |

**Apply.** Containeren opretter admin-kontoen ved start. Log ind med præcis den e-mail og det password.

`ADMIN_PASSWORD` er sandheden i templaten: skifter du password i appen, sættes det tilbage ved næste container-start, så længe feltet er udfyldt.

### 4. Anbefalede felter

| Felt | Variabel | Note |
|---|---|---|
| Gemini API Key | `GEMINI_API_KEY` | Bedre Scan. Uden nøgle bruges Tesseract (DA/EN) |
| Public Base URL | `PUBLIC_BASE_URL` | `https://beannote.ditdomæne.dk` — **påkrævet** ved SWAG/OAuth. Ingen trailing slash |

Lad **Web Workers** stå på `1` og **Job Workers** på `0`.

### 5. Tjek at den kører

- Docker-fanen: container **healthy**
- WebUI, eller `http://TOWER-IP:8502` — du skal se login-skærmen
- `curl -fsS http://TOWER-IP:8502/api/health` skal give `"ok": true`, `"environment": "production"`, `"auto_flush": false`

Loop-restart i loggen betyder næsten altid at `JWT_SECRET` mangler.

**Login over ren HTTP virker** på LAN. Sætter du `PUBLIC_BASE_URL=https://…`, skal du bruge HTTPS-adressen — cookien bliver Secure.

---

## SWAG reverse proxy (HTTPS)

Brug den færdige nginx-fil: [`unraid/swag/beannote.subdomain.conf`](unraid/swag/beannote.subdomain.conf)

1. Kopiér filen til  
   `/mnt/user/appdata/swag/nginx/proxy-confs/beannote.subdomain.conf`
2. Sæt BeanNote og SWAG på **samme Docker-netværk** (custom bridge, fx `proxynet`).
3. DNS: `beannote.ditdomæne.dk` → din WAN-IP (CNAME eller A).
4. I BeanNote: `PUBLIC_BASE_URL=https://beannote.ditdomæne.dk`
5. Genstart SWAG og BeanNote.

Proxyen rammer container-navnet `beannote` på port `8502`, tillader 16 MB uploads og 5 min timeout til scan. Luk host-port 8502 udefra; SWAG er indgangen.

Åbn appen på telefonen via **HTTPS**, og tilføj til hjemmeskærm (PWA). Kamera til Scan kræver HTTPS.

### Google / Apple (valgfrit)

Redirect URI skal matche `PUBLIC_BASE_URL` præcist:

- Google: `https://beannote.ditdomæne.dk/api/auth/google/callback`
- Apple: `https://beannote.ditdomæne.dk/api/auth/apple/callback`

Apple `.p8`-nøgle ind i `APPLE_PRIVATE_KEY` som **én linje** med `\n` i stedet for linjeskift.

---

## Opdatering og backup

- **Opdater:** Docker → beannote → force update (`latest`). Volume’et røres ikke.
- **Backup:** kopiér hele `/mnt/cache/appdata/beannote/data/` (eller brug Appdata Backup). Stop containeren først, eller lad backup-pluginet køre, så WAL er flushed.

Map **ikke** `/app/static`. Katalogfotos du selv uploader ligger i volume under `catalog/`.

---

## Det du ikke skal gøre

- Sæt ikke `ENVIRONMENT=local` — det tømmer databasen ved start
- Sæt ikke `RESET_DB_ON_START=true`
- Læg ikke SQLite på `/mnt/user/...` (FUSE/shfs) — brug cache-disk
- Åbn ikke port 8502 direkte til internettet

---

## Docker Compose

Samme image og env som templaten. Se [`docker-compose.yml`](docker-compose.yml). På Unraid: foretræk cache-stien frem for `/mnt/user/appdata/...`.
