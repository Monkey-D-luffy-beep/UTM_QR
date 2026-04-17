"""
main.py — FastAPI application entry point.

Endpoints
---------
GET  /health                      → liveness probe (no auth)
GET  /r/{slug}                    → QR redirect (no auth, NEVER fails)
GET  /admin/links                 → list all links   (API-key)
POST /admin/links                 → create a link    (API-key)
PUT  /admin/links/{slug}          → update a link    (API-key)
DEL  /admin/links/{slug}          → delete a link    (API-key) ← caution
GET  /admin/links/{slug}/stats    → click stats      (API-key)

Auth
----
Pass  X-API-Key: <ADMIN_API_KEY>  header on every /admin/* request.
Set ADMIN_API_KEY env-var before deployment (see .env.example).

Failsafe guarantee
------------------
/r/{slug} ALWAYS issues a 302 redirect.  If anything goes wrong
(DB down, slug missing, any exception) it falls back to FALLBACK_URL.
"""

import logging
import logging.handlers
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()  # must run before reading os.getenv() below
from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

import database
import models
import schemas

# ── Environment ───────────────────────────────────────────────────────────────
FALLBACK_URL: str = os.getenv(
    "FALLBACK_URL", "https://forms.gle/mER9B21dKyLRjA9v5"
)
ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", "change-me-before-deploy")

# ── Logging ───────────────────────────────────────────────────────────────────
_LOG_DIR = os.getenv("LOG_DIR", "logs")
os.makedirs(_LOG_DIR, exist_ok=True)

_handlers: list[logging.Handler] = [logging.StreamHandler()]
try:
    _file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(_LOG_DIR, "app.log"),
        maxBytes=5_000_000,   # 5 MB per file
        backupCount=5,
    )
    _handlers.append(_file_handler)
except OSError:
    pass  # read-only filesystem (e.g. some serverless envs) → stdout only

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=_handlers,
)
logger = logging.getLogger(__name__)


# ── Application lifespan ──────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Create DB tables on startup; nothing to teardown."""
    models.Base.metadata.create_all(bind=database.engine)
    logger.info("Database tables initialised.")
    if ADMIN_API_KEY == "change-me-before-deploy":
        logger.warning(
            "ADMIN_API_KEY is still the default — set it via env-var before going live!"
        )
    yield


app = FastAPI(
    title="QR Redirect & Tracking Service",
    version="1.0.0",
    lifespan=lifespan,
    # Hide /docs and /openapi.json in production if desired via env-var.
    docs_url="/docs" if os.getenv("ENABLE_DOCS", "true").lower() == "true" else None,
    redoc_url=None,
)


# ── Helpers ───────────────────────────────────────────────────────────────────
# User-agent substrings that identify monitoring bots (case-insensitive).
# Clicks from these agents are stored in DB but excluded from human counts.
_BOT_UA_PATTERNS = (
    "uptimerobot",
    "pingdom",
    "statuscake",
    "googlebot",
    "bingbot",
    "facebookexternalhit",
    "twitterbot",
    "whatsapp",
    "slackbot",
    "curl/",
    "python-httpx",
    "python-requests",
    "go-http-client",
)


def _is_bot(user_agent: str) -> bool:
    ua = user_agent.lower()
    return any(pat in ua for pat in _BOT_UA_PATTERNS)


def _real_ip(request: Request) -> str:
    """Extract the real client IP, respecting X-Forwarded-For (Render / nginx)."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _verify_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> None:
    """Dependency: raise 403 if the API key is wrong."""
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")


# ── Public endpoints ──────────────────────────────────────────────────────────
@app.get("/health", tags=["ops"])
def health():
    """Liveness probe — always returns 200."""
    return {"status": "ok", "utc": datetime.utcnow().isoformat()}


@app.get("/r/{slug}", tags=["redirect"])
def redirect(slug: str, request: Request, db: Session = Depends(database.get_db)):
    """
    Core QR redirect endpoint.

    CRITICAL CONTRACT — this handler MUST always issue a 302 redirect.
    It catches every possible exception and falls back to FALLBACK_URL.
    """
    ip = _real_ip(request)
    ua = request.headers.get("user-agent", "")[:512]

    try:
        link = (
            db.query(models.QRLink)
            .filter(models.QRLink.slug == slug)
            .first()
        )

        if not link:
            logger.warning("SLUG_NOT_FOUND slug=%s ip=%s ua=%s", slug, ip, ua[:80])
            return RedirectResponse(url=FALLBACK_URL, status_code=302)

        destination = link.destination_url

        # ── Log the click (failures here must NOT break the redirect) ──────
        try:
            click = models.QRClick(
                link_id=link.id,
                timestamp=datetime.utcnow(),
                ip=ip,
                user_agent=ua,
            )
            db.add(click)
            db.commit()
        except Exception as click_err:
            logger.error("CLICK_LOG_FAILED slug=%s err=%r", slug, click_err)
            try:
                db.rollback()
            except Exception:
                pass

        logger.info("REDIRECT slug=%s → %s ip=%s", slug, destination, ip)
        return RedirectResponse(url=destination, status_code=302)

    except Exception as exc:
        # Absolute last resort — never let an exception surface to the user.
        logger.error("REDIRECT_EXCEPTION slug=%s err=%r ip=%s", slug, exc, ip)
        return RedirectResponse(url=FALLBACK_URL, status_code=302)


# ── Admin endpoints (API-key protected) ───────────────────────────────────────
_admin_deps = [Depends(_verify_api_key)]


@app.get("/admin/links", tags=["admin"], dependencies=_admin_deps)
def list_links(db: Session = Depends(database.get_db)) -> list[schemas.LinkOut]:
    """Return all registered slugs."""
    links = db.query(models.QRLink).order_by(models.QRLink.created_at).all()
    return [schemas.LinkOut.model_validate(lnk) for lnk in links]


@app.post(
    "/admin/links",
    tags=["admin"],
    dependencies=_admin_deps,
    status_code=201,
)
def create_link(
    payload: schemas.LinkCreate,
    db: Session = Depends(database.get_db),
) -> schemas.LinkOut:
    """Register a new slug → destination_url mapping."""
    if db.query(models.QRLink).filter(models.QRLink.slug == payload.slug).first():
        raise HTTPException(status_code=409, detail=f"Slug '{payload.slug}' already exists")

    link = models.QRLink(
        slug=payload.slug,
        destination_url=payload.destination_url,
        created_at=datetime.utcnow(),
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    logger.info("LINK_CREATED slug=%s", payload.slug)
    return schemas.LinkOut.model_validate(link)


@app.put("/admin/links/{slug}", tags=["admin"], dependencies=_admin_deps)
def update_link(
    slug: str,
    payload: schemas.LinkUpdate,
    db: Session = Depends(database.get_db),
) -> schemas.LinkOut:
    """Update the destination URL for an existing slug (QR code stays the same)."""
    link = db.query(models.QRLink).filter(models.QRLink.slug == slug).first()
    if not link:
        raise HTTPException(status_code=404, detail=f"Slug '{slug}' not found")

    old_url = link.destination_url
    link.destination_url = payload.destination_url
    db.commit()
    db.refresh(link)
    logger.info("LINK_UPDATED slug=%s old=%s new=%s", slug, old_url, payload.destination_url)
    return schemas.LinkOut.model_validate(link)


@app.delete("/admin/links/{slug}", tags=["admin"], dependencies=_admin_deps, status_code=204)
def delete_link(slug: str, db: Session = Depends(database.get_db)) -> None:
    """
    Delete a slug and all its click history.
    WARNING: printed QR codes pointing to this slug will fall back to FALLBACK_URL.
    """
    link = db.query(models.QRLink).filter(models.QRLink.slug == slug).first()
    if not link:
        raise HTTPException(status_code=404, detail=f"Slug '{slug}' not found")

    db.delete(link)
    db.commit()
    logger.warning("LINK_DELETED slug=%s", slug)


@app.get("/admin/links/{slug}/stats", tags=["admin"], dependencies=_admin_deps)
def link_stats(
    slug: str,
    db: Session = Depends(database.get_db),
) -> schemas.StatsOut:
    """Return click stats, separating real human scans from monitoring bots."""
    link = db.query(models.QRLink).filter(models.QRLink.slug == slug).first()
    if not link:
        raise HTTPException(status_code=404, detail=f"Slug '{slug}' not found")

    all_clicks = (
        db.query(models.QRClick)
        .filter(models.QRClick.link_id == link.id)
        .order_by(models.QRClick.timestamp.desc())
        .all()
    )
    human_clicks = [c for c in all_clicks if not _is_bot(c.user_agent)]
    bot_clicks   = [c for c in all_clicks if _is_bot(c.user_agent)]

    return schemas.StatsOut(
        slug=slug,
        destination_url=link.destination_url,
        total_clicks=len(human_clicks),
        bot_clicks=len(bot_clicks),
        recent_clicks=[schemas.ClickOut.model_validate(c) for c in human_clicks[:200]],
    )


# ── Admin dashboard (HTML, browser-accessible) ────────────────────────────────
from fastapi.responses import HTMLResponse  # noqa: E402
from fastapi import Query                   # noqa: E402

_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>QR Dashboard — Login</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
          background:#0f172a;color:#e2e8f0;min-height:100vh;
          display:flex;align-items:center;justify-content:center}}
    .box{{background:#1e293b;border-radius:1rem;padding:2.5rem 2rem;width:100%;max-width:360px}}
    h1{{font-size:1.25rem;margin-bottom:.5rem;color:#f8fafc}}
    p{{color:#94a3b8;font-size:.875rem;margin-bottom:1.5rem}}
    input{{width:100%;padding:.75rem 1rem;background:#0f172a;border:1px solid #334155;
           border-radius:.5rem;color:#f1f5f9;font-size:1rem;margin-bottom:1rem}}
    input:focus{{outline:none;border-color:#38bdf8}}
    button{{width:100%;padding:.75rem;background:#0ea5e9;border:none;border-radius:.5rem;
            color:#fff;font-size:1rem;font-weight:600;cursor:pointer}}
    button:hover{{background:#0284c7}}
    .err{{color:#f87171;font-size:.85rem;margin-top:.75rem;text-align:center}}
  </style>
</head>
<body>
  <div class="box">
    <h1>QR Scan Dashboard</h1>
    <p>Enter your admin API key to view scan stats.</p>
    <form method="get" action="/admin/dashboard">
      <input type="password" name="key" placeholder="Admin API Key" required autofocus>
      <button type="submit">View Dashboard</button>
      {error}
    </form>
  </div>
</body>
</html>"""


@app.get("/admin/dashboard", tags=["admin"], response_class=HTMLResponse,
         include_in_schema=False)
def dashboard(key: str = Query(default=""), db: Session = Depends(database.get_db)):
    """
    Browser-accessible dashboard.
    Auth via ?key=YOUR_API_KEY query param (submitted by the login form).
    """
    # ── Auth ──────────────────────────────────────────────────────────────────
    if not key or key != ADMIN_API_KEY:
        error_msg = '<p class="err">Incorrect key — try again.</p>' if key else ""
        return HTMLResponse(
            content=_LOGIN_PAGE.format(error=error_msg),
            status_code=200,
        )

    # ── Data ──────────────────────────────────────────────────────────────────
    links = db.query(models.QRLink).order_by(models.QRLink.created_at).all()

    rows = ""
    for link in links:
        all_clicks = (
            db.query(models.QRClick)
            .filter(models.QRClick.link_id == link.id)
            .all()
        )
        human  = sum(1 for c in all_clicks if not _is_bot(c.user_agent))
        bots   = len(all_clicks) - human
        last   = max((c.timestamp for c in all_clicks if not _is_bot(c.user_agent)),
                     default=None)
        last_s = last.strftime("%d %b %Y %H:%M") if last else "—"
        slug_safe = link.slug.replace("<", "&lt;").replace(">", "&gt;")
        rows += f"""
        <tr>
          <td><code>{slug_safe}</code></td>
          <td class="num">{human}</td>
          <td class="num muted">{bots}</td>
          <td class="muted">{last_s}</td>
          <td class="url">{link.destination_url[:70]}…</td>
        </tr>"""

    if not rows:
        rows = '<tr><td colspan="5" style="text-align:center;color:#475569;padding:2rem">No slugs yet — add them via the API.</td></tr>'

    total_human = sum(
        1 for lnk in links
        for c in db.query(models.QRClick).filter(models.QRClick.link_id == lnk.id).all()
        if not _is_bot(c.user_agent)
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="refresh" content="60">
  <title>QR Dashboard</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
          background:#0f172a;color:#e2e8f0;padding:2rem}}
    h1{{font-size:1.5rem;margin-bottom:.25rem;color:#f8fafc}}
    .sub{{color:#94a3b8;font-size:.875rem;margin-bottom:2rem;line-height:1.6}}
    .cards{{display:flex;gap:1rem;margin-bottom:2rem;flex-wrap:wrap}}
    .card{{background:#1e293b;border-radius:.75rem;padding:1.25rem 1.5rem;flex:1;min-width:160px}}
    .card-label{{font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;color:#64748b}}
    .card-val{{font-size:2rem;font-weight:700;color:#38bdf8;margin-top:.25rem}}
    .table-wrap{{background:#1e293b;border-radius:.75rem;overflow:hidden}}
    table{{width:100%;border-collapse:collapse}}
    th{{background:#334155;text-align:left;padding:.75rem 1rem;
        font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;color:#94a3b8}}
    td{{padding:.75rem 1rem;border-top:1px solid #334155;font-size:.875rem;vertical-align:middle}}
    tr:hover td{{background:#243044}}
    .num{{font-weight:700;font-size:1.1rem;color:#38bdf8}}
    .muted{{color:#64748b}}
    .url{{font-size:.75rem;color:#64748b;max-width:320px;overflow:hidden;
           text-overflow:ellipsis;white-space:nowrap}}
    .footer{{margin-top:1rem;font-size:.75rem;color:#475569;
              display:flex;justify-content:space-between;align-items:center}}
    .tip{{background:#1e293b;border-radius:.5rem;padding:1rem 1.25rem;
           margin-bottom:2rem;font-size:.8rem;color:#94a3b8;border-left:3px solid #0ea5e9}}
    .tip strong{{color:#e2e8f0}}
    a{{color:#38bdf8;text-decoration:none}}
  </style>
</head>
<body>
  <h1>QR Scan Dashboard</h1>
  <p class="sub">
    Real human scans only — UptimeRobot &amp; other bots are filtered out automatically.<br>
    Page auto-refreshes every 60 seconds.
  </p>

  <div class="tip">
    <strong>Want to track form submissions too?</strong>
    Open your Google Form → <strong>Responses</strong> tab → click the green Sheets icon to export to Google Sheets.
    The <code>entry.222</code> column will show which table/slug submitted — cross-reference with scans above.
  </div>

  <div class="cards">
    <div class="card">
      <div class="card-label">Total Human Scans</div>
      <div class="card-val">{total_human}</div>
    </div>
    <div class="card">
      <div class="card-label">Active QR Codes</div>
      <div class="card-val">{len(links)}</div>
    </div>
    <div class="card">
      <div class="card-label">Generated</div>
      <div class="card-val" style="font-size:1rem;padding-top:.4rem">
        {datetime.utcnow().strftime('%d %b %Y')}<br>
        <span style="font-size:.75rem;color:#64748b">{datetime.utcnow().strftime('%H:%M UTC')}</span>
      </div>
    </div>
  </div>

  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Slug (QR Code)</th>
          <th>Human Scans</th>
          <th>Bot Pings</th>
          <th>Last Real Scan</th>
          <th>Destination URL</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>

  <div class="footer">
    <span>Bookmark: <code>{{}}</code> — refreshes every 60s</span>
    <a href="/docs">API docs</a>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)
