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
    """Return total click count and the 200 most recent clicks for a slug."""
    link = db.query(models.QRLink).filter(models.QRLink.slug == slug).first()
    if not link:
        raise HTTPException(status_code=404, detail=f"Slug '{slug}' not found")

    total = db.query(models.QRClick).filter(models.QRClick.link_id == link.id).count()
    recent = (
        db.query(models.QRClick)
        .filter(models.QRClick.link_id == link.id)
        .order_by(models.QRClick.timestamp.desc())
        .limit(200)
        .all()
    )
    return schemas.StatsOut(
        slug=slug,
        destination_url=link.destination_url,
        total_clicks=total,
        recent_clicks=[schemas.ClickOut.model_validate(c) for c in recent],
    )
