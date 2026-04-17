#!/usr/bin/env python3
"""
seed_data.py — Populate the database with initial slug → destination_url mappings.

Edit the LINKS list below, then run:
    python seed_data.py

Running it again is safe — existing slugs are skipped, not overwritten.
To update a destination URL use the admin API:
    PUT /admin/links/{slug}   with header X-API-Key: <your key>
"""

from datetime import datetime

from database import SessionLocal, engine
from models import Base, QRLink

# ── Edit this list ────────────────────────────────────────────────────────────
# Replace FORM_ID and entry.* values with your actual Google Form identifiers.
# The slug must match whatever you encoded in the printed QR code.
LINKS: list[dict] = [
    {
        "slug": "table_1",
        "destination_url": (
            "https://docs.google.com/forms/d/e/FORM_ID/viewform"
            "?entry.111=qr&entry.222=table_1"
        ),
    },
    {
        "slug": "table_2",
        "destination_url": (
            "https://docs.google.com/forms/d/e/FORM_ID/viewform"
            "?entry.111=qr&entry.222=table_2"
        ),
    },
    {
        "slug": "table_3",
        "destination_url": (
            "https://docs.google.com/forms/d/e/FORM_ID/viewform"
            "?entry.111=qr&entry.222=table_3"
        ),
    },
    # Add more rows as needed…
]
# ─────────────────────────────────────────────────────────────────────────────


def seed() -> None:
    # Ensure tables exist (safe to call even if they already exist)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    added = 0
    skipped = 0
    try:
        for item in LINKS:
            existing = (
                db.query(QRLink).filter(QRLink.slug == item["slug"]).first()
            )
            if existing:
                print(f"  SKIP  '{item['slug']}' — already exists")
                skipped += 1
            else:
                link = QRLink(
                    slug=item["slug"],
                    destination_url=item["destination_url"],
                    created_at=datetime.utcnow(),
                )
                db.add(link)
                print(f"  ADD   '{item['slug']}'  →  {item['destination_url'][:60]}…")
                added += 1
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"\nERROR: {exc}")
        raise
    finally:
        db.close()

    print(f"\nDone — {added} added, {skipped} skipped.")


if __name__ == "__main__":
    seed()
