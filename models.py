"""
models.py — SQLAlchemy ORM models.

Tables
------
qr_links  : one row per printed QR code slug
qr_clicks : one row per scan / HTTP hit on /r/{slug}
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class QRLink(Base):
    __tablename__ = "qr_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    destination_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    clicks: Mapped[list["QRClick"]] = relationship(
        "QRClick", back_populates="link", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<QRLink slug={self.slug!r}>"


class QRClick(Base):
    __tablename__ = "qr_clicks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    link_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("qr_links.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )
    ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(Text, nullable=False, default="")

    link: Mapped["QRLink"] = relationship("QRLink", back_populates="clicks")

    def __repr__(self) -> str:
        return f"<QRClick link_id={self.link_id} ts={self.timestamp}>"
