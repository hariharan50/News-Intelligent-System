"""Public reference data the app needs to build profile editors: taxonomy, templates, sources."""

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionDep
from app.models import Source
from app.processing.taxonomy import ENTITIES, EVENT_TYPES, TAXONOMY_VERSION, TOPICS
from app.profiles.service import REGIONS
from app.scoring.templates import TEMPLATES

router = APIRouter(prefix="/meta", tags=["meta"])


class NamedItem(BaseModel):
    slug: str
    name: str


class EntityItem(NamedItem):
    kind: str
    ticker: str | None
    region: str | None


class EventTypeItem(NamedItem):
    routine: bool


class TaxonomyOut(BaseModel):
    version: int
    topics: list[NamedItem]
    entities: list[EntityItem]
    event_types: list[EventTypeItem]
    regions: list[str]


class TemplateOut(BaseModel):
    slug: str
    name: str
    description: str
    show_threshold: int
    alert_threshold: int
    rule_count: int


class SourceItem(NamedItem):
    category: str
    region: str
    enabled: bool


@router.get("/taxonomy", response_model=TaxonomyOut)
async def taxonomy() -> TaxonomyOut:
    return TaxonomyOut(
        version=TAXONOMY_VERSION,
        topics=[NamedItem(slug=t.slug, name=t.name) for t in TOPICS],
        entities=[
            EntityItem(slug=e.slug, name=e.name, kind=e.kind, ticker=e.ticker, region=e.region)
            for e in ENTITIES
        ],
        event_types=[
            EventTypeItem(slug=e.slug, name=e.name, routine=e.routine) for e in EVENT_TYPES
        ],
        regions=sorted(REGIONS),
    )


@router.get("/templates", response_model=list[TemplateOut])
async def templates() -> list[TemplateOut]:
    return [
        TemplateOut(
            slug=t.slug,
            name=t.name,
            description=t.description,
            show_threshold=t.show_threshold,
            alert_threshold=t.alert_threshold,
            rule_count=len(t.rules),
        )
        for t in TEMPLATES.values()
    ]


@router.get("/sources", response_model=list[SourceItem])
async def sources(db: SessionDep) -> list[SourceItem]:
    rows = await db.scalars(select(Source).order_by(Source.category, Source.name))
    return [
        SourceItem(
            slug=s.slug, name=s.name, category=s.category, region=s.region, enabled=s.enabled
        )
        for s in rows
    ]


@router.get("/features")
async def features() -> dict:
    """Which optional parts of the product are switched on (the app hides the rest)."""
    s = get_settings()
    return {"billing": s.feature_billing, "ai": s.feature_ai, "google": s.google_configured}
