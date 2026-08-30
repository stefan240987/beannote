"""Pydantic request bodies for BeanNote API routes."""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from db import _as_float


class EmailAuthIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    username: str = ""


class BeanIn(BaseModel):
    name: str
    roaster: str
    origin: str = ""
    process: str = ""
    roast_level: str = ""
    roaster_notes: str = ""
    flavor_tags: Any = Field(default_factory=dict)
    suitable_for: list[str] = Field(default_factory=list)
    story: Any = ""
    image_url: str = ""
    roaster_url: str = ""
    recommended_method: str = ""
    grind_size: str = ""
    water_temp: str = ""
    brew_ratio: str = ""
    brew_recommendation: Any = Field(default_factory=dict)
    roast_date: str = ""
    altitude: str = ""
    varietal: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    region_full: str = ""
    acidity_score: Optional[int] = None
    body_score: Optional[int] = None
    roast_level_score: Optional[int] = None
    roaster_acidity: Optional[int] = None
    roaster_body: Optional[int] = None
    roaster_roast_level: Optional[int] = None
    skip_fuzzy: bool = False


class RatingIn(BaseModel):
    bean_id: int
    brew_method: str = "V60"
    rating: float = 4.0
    acidity: float = 3.0
    sweetness: float = 3.0
    body: float = 3.0
    aftertaste: float = 3.0
    notes: str = ""
    grind_setting: str = ""
    coffee_grams: Optional[float] = None
    water_grams: Optional[float] = None
    brew_time: str = ""
    tasting_notes_user: str = ""
    espresso_machine: str = ""
    grinder: str = ""

    @field_validator("coffee_grams", "water_grams", mode="before")
    @classmethod
    def locale_grams(cls, value: Any) -> float | None:
        return _as_float(value)


class GearLookupIn(BaseModel):
    query: str = Field(default="", max_length=120)
    kind: str = ""
    lang: str = ""


class GearIn(BaseModel):
    espresso_machine: str = ""
    grinder: str = ""
    brewer_types: list[str] = Field(default_factory=list)
    gear_specs: Any = Field(default_factory=list)


class GearCreateIn(BaseModel):
    brand: str = Field(default="", max_length=80)
    name: str = Field(min_length=1, max_length=120)
    type: str = Field(min_length=1, max_length=40)
    kind: str = ""
    highlights: Any = Field(default_factory=list)
    specs: Any = Field(default_factory=dict)

    @field_validator("brand", "name", "type", "kind", mode="before")
    @classmethod
    def strip_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("highlights", mode="before")
    @classmethod
    def parse_highlights(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return [part.strip() for part in value.split(",") if part.strip()][:8]
        if isinstance(value, list):
            return [str(part).strip() for part in value if str(part).strip()][:8]
        return []

    @field_validator("specs", mode="before")
    @classmethod
    def parse_specs(cls, value: Any) -> dict[str, Any]:
        if value is None or value == "":
            return {}
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return {}
        return value if isinstance(value, dict) else {}
