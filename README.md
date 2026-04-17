# UTM-QR — Production QR Redirect & Tracking Service

A minimal, fail-safe FastAPI service that maps short slugs (encoded in printed QR codes) to Google Form prefilled URLs, with click tracking and a live-editable admin API.

---

## Project structure

```
UTM-QR/
├── main.py            # FastAPI app — all routes
├── database.py        # SQLAlchemy engine + session
├── models.py          # ORM models (qr_links, qr_clicks)
├── schemas.py         # Pydantic request/response shapes
├── qr_generator.py    # CLI to generate QR code PNGs
├── seed_data.py       # Seed initial slug data
├── requirements.txt
├── Procfile           # For Render / Heroku-style hosts
├── render.yaml        # One-click Render deploy config
├── .env.example       # Environment variable reference
└── .gitignore
```

---

## Quickstart (local)

```bash
# 1. Clone / enter the project folder
cd UTM-QR

# 2. Create and activate a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy .env.example and set your values
copy .env.example .env      # Windows
# cp .env.example .env      # macOS/Linux

# 5. Edit .env — set ADMIN_API_KEY to a strong random string

# 6. Seed initial slugs (edit LINKS in seed_data.py first)
python seed_data.py

# 7. Start the server
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000/health to verify.  
Interactive API docs: http://localhost:8000/docs

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ADMIN_API_KEY` | **Yes** | `change-me-before-deploy` | API key for all `/admin/*` routes |
| `FALLBACK_URL` | No | `https://forms.gle/mER9B21dKyLRjA9v5` | URL to redirect to if slug is missing or any error occurs |
| `DATABASE_URL` | No | `sqlite:///./qr_redirects.db` | SQLAlchemy connection string |
| `LOG_DIR` | No | `logs` | Directory for rotating log files |
| `ENABLE_DOCS` | No | `true` | Set `false` to hide `/docs` in production |

---

## Admin API

All endpoints require the header: `X-API-Key: <ADMIN_API_KEY>`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/admin/links` | List all slugs |
| `POST` | `/admin/links` | Create a new slug |
| `PUT` | `/admin/links/{slug}` | Update destination URL (QR stays the same) |
| `DELETE` | `/admin/links/{slug}` | Remove a slug (careful — printed QRs will fall back) |
| `GET` | `/admin/links/{slug}/stats` | Click count + last 200 scans |

### Create a link

```bash
curl -X POST https://qr.yourdomain.com/admin/links \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "slug": "table_5",
    "destination_url": "https://docs.google.com/forms/d/e/FORM_ID/viewform?entry.111=qr&entry.222=table_5"
  }'
```

### Update a destination URL (live, no reprint needed)

```bash
curl -X PUT https://qr.yourdomain.com/admin/links/table_5 \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"destination_url": "https://new-form-url.example.com"}'
```

---

## Generating QR codes

```bash
# For specific slugs
python qr_generator.py --base-url https://qr.yourdomain.com --slugs table_1 table_2 table_3

# For every slug in the database
python qr_generator.py --base-url https://qr.yourdomain.com --from-db

# Custom output directory
python qr_generator.py --base-url https://qr.yourdomain.com --from-db --out ./print_ready
```

PNGs are saved to `qr_codes/` by default.  
Settings: `ERROR_CORRECT_H`, `box_size=12`, `border=4` — print at ≥ 300 DPI.

---

## Deployment — Render (recommended, free tier)

Render is the easiest option: persistent disk + free HTTPS + auto-deploy from GitHub.

### Steps

1. Push the project to a GitHub repository.
2. Go to [render.com](https://render.com) → **New → Blueprint** → connect your repo.  
   Render reads `render.yaml` automatically.
3. After deploy, find `ADMIN_API_KEY` in the service's **Environment** tab (auto-generated).
4. Seed the database via SSH or the Render shell:
   ```bash
   python seed_data.py
   ```
5. Set your custom domain under the service's **Settings → Custom Domains**.

### Domain setup (Render)

```
CNAME  qr.yourdomain.com  →  <your-service>.onrender.com
```

Add in your DNS provider (Cloudflare, Route 53, etc.). Render issues a free TLS certificate automatically.

---

## Deployment — AWS EC2 Free Tier (most reliable long-term)

Use an EC2 `t2.micro` running Ubuntu 22.04. SQLite lives on the attached EBS volume — no extra cost.

### 1. Launch the instance

- AMI: Ubuntu 22.04 LTS
- Instance type: `t2.micro` (free tier, 12 months)
- Storage: 8 GB gp2 EBS (free tier)
- Security Group: allow inbound TCP 22 (SSH), 80 (HTTP), 443 (HTTPS)

### 2. Server setup

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx git

# Clone your repo
git clone https://github.com/YOUR_USER/UTM-QR.git /srv/utm-qr
cd /srv/utm-qr

# Virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Environment variables
cp .env.example .env
nano .env      # set ADMIN_API_KEY and DATABASE_URL

# Seed data
python seed_data.py
```

### 3. Systemd service

```bash
sudo nano /etc/systemd/system/utm-qr.service
```

Paste:

```ini
[Unit]
Description=UTM QR Redirect Service
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/srv/utm-qr
EnvironmentFile=/srv/utm-qr/.env
ExecStart=/srv/utm-qr/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now utm-qr
sudo systemctl status utm-qr
```

### 4. Nginx reverse proxy

```bash
sudo nano /etc/nginx/sites-available/utm-qr
```

Paste:

```nginx
server {
    listen 80;
    server_name qr.yourdomain.com;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/utm-qr /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### 5. Free HTTPS with Let's Encrypt

```bash
sudo certbot --nginx -d qr.yourdomain.com
# Auto-renewal is configured automatically
```

### 6. DNS record

```
A  qr.yourdomain.com  →  <EC2 public IP>
```

Use an **Elastic IP** to prevent the IP changing on instance restart.

---

## Keeping QR codes working forever

| Situation | Action |
|---|---|
| Google Form URL changes | `PUT /admin/links/{slug}` — no reprint needed |
| Server moves to new IP | Update DNS A record; slug → URL mapping is unchanged |
| Database backup | `cp qr_redirects.db qr_redirects.db.bak` (SQLite is a single file) |
| High scan volume | Upgrade to PostgreSQL by changing `DATABASE_URL`; no code changes needed |

---

## Failsafe guarantee

Every scan of `/r/{slug}` is wrapped in a try/except.  
Any failure (DB down, slug not found, exception) issues `302 → FALLBACK_URL`.  
**QR codes printed today will work forever.**
