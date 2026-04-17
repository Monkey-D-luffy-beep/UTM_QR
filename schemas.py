"""
schemas.py — Pydantic request / response models for the admin API.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class LinkCreate(BaseModel):
    slug: str
    destination_url: str

    @field_validator("slug")
    @classmethod
    def slug_no_spaces(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("slug must not be empty")
        if " " in v:
            raise ValueError("slug must not contain spaces")
        return v

    @field_validator("destination_url")
    @classmethod
    def url_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("destination_url must not be empty")
        return v.strip()


class LinkUpdate(BaseModel):
    destination_url: str

    @field_validator("destination_url")
    @classmethod
    def url_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("destination_url must not be empty")
        return v.strip()


class LinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    destination_url: str
    created_at: datetime


class ClickOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    link_id: int
    timestamp: datetime
    ip: str
    user_agent: str
    device_type: str = "unknown"
    os_family: str = "unknown"
    browser: str = "unknown"


class StatsOut(BaseModel):
    slug: str
    destination_url: str
    total_clicks: int    # human scans only (bots excluded)
    bot_clicks: int      # monitoring pings (UptimeRobot etc.)
    recent_clicks: list[ClickOut]
