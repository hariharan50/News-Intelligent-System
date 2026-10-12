"""The only module that talks to Claude. Everything else uses the Summarizer protocol, so
tests (and a future second provider) can swap it out."""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

import anthropic
from pydantic import BaseModel, Field

from app.core.config import get_settings

log = logging.getLogger(__name__)

# USD per million tokens: (input, output). Unknown models are priced at the most expensive
# row so the budget errs on the safe side.
PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-5-5": (Decimal("4"), Decimal("20")),
    "claude-opus-5": (Decimal("5"), Decimal("25")),
    "claude-opus-4-8": (Decimal("5"), Decimal("25")),
    "claude-sonnet-5-5": (Decimal("2"), Decimal("10")),
    "claude-sonnet-5": (Decimal("2"), Decimal("10")),
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),
    "claude-fable-5-1": (Decimal("10"), Decimal("50")),
}
_FALLBACK_PRICE = (Decimal("10"), Decimal("50"))
_MILLION = Decimal(1_000_000)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    price_in, price_out = PRICES.get(model, _FALLBACK_PRICE)
    return (input_tokens * price_in + output_tokens * price_out) / _MILLION


# --------------------------------------------------------------------------- input / output


@dataclass(frozen=True, slots=True)
class ReportIn:
    source: str
    title: str
    excerpt: str | None
    published_at: datetime


@dataclass(frozen=True, slots=True)
class StoryIn:
    title: str
    event_type: str | None
    entities: list[str]
    reports: list[ReportIn]


class SummaryOut(BaseModel):
    """What Claude must return (enforced with structured outputs)."""

    summary: str = Field(description="One or two plain sentences, at most 45 words.")
    key_facts: list[str] = Field(
        description="Up to 4 short facts with the concrete numbers, dates or names given."
    )
    affected: list[str] = Field(
        description="Up to 4 markets, sectors or assets this may move, e.g. 'Indian banks', "
        "'USD/INR', 'Crude oil'. Empty if unclear."
    )
    market_impact: Literal[1, 2, 3, 4, 5] = Field(
        description="Likely impact on Indian markets: 1 minor, 2 limited, 3 moderate, "
        "4 major, 5 very major."
    )


@dataclass
class SummaryResult:
    status: str  # "ok" | "refused" | "failed"
    output: SummaryOut | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = field(default_factory=lambda: Decimal(0))
    error: str | None = None


class Summarizer(Protocol):
    async def summarize(self, story: StoryIn) -> SummaryResult: ...


# --------------------------------------------------------------------------- prompt

SYSTEM_PROMPT = """\
You write the summary line for a news story in a market-news app used by Indian traders.

You receive the headline and the short feed excerpts from one or more outlets reporting the
same event. Write from that text only: do not add facts, figures, background or dates that
are not in it, and do not guess at what happened next. If the reports disagree, say so
briefly. If the text says little beyond the headline, keep the summary short rather than
padding it.

- summary: one or two plain sentences (at most 45 words) saying what happened and the key
  number, if any. No hype, no "breaking", no opinions.
- key_facts: up to 4 short facts that carry concrete numbers, dates, names or decisions
  from the text. Leave it empty rather than repeat the summary.
- affected: up to 4 markets, sectors or assets a trader would watch because of this
  (for example "Indian banks", "USD/INR", "Crude oil", "IT stocks"). This is labelled as
  an opinion in the app. Never give buy/sell advice or price targets.
- market_impact: how much this is likely to matter to traders in Indian markets (equities,
  rupee, bonds, commodities), judged from the text only:
  1 minor: no realistic market effect (routine notices, commentary, small local news);
  2 limited: matters to one small company or a niche;
  3 moderate: moves a sector, a large company, or a market at the margin;
  4 major: likely to move a broad market, a key rate or the rupee noticeably;
  5 very major: rare. A surprise policy decision, war, or crisis-level event.
  Most stories are 2 or 3. Say 4 or 5 only when the text supports it.
Write in English, even if a report is in another language."""

_MAX_REPORTS = 6
_MAX_EXCERPT_CHARS = 600


def build_prompt(story: StoryIn) -> str:
    lines = [f"Headline: {story.title}"]
    if story.event_type:
        lines.append(f"Event type (our tag): {story.event_type.replace('_', ' ')}")
    if story.entities:
        lines.append(f"Mentions (our tags): {', '.join(story.entities[:8])}")
    lines.append("")
    for i, r in enumerate(story.reports[:_MAX_REPORTS], 1):
        lines.append(f"Report {i} - {r.source}, {r.published_at:%d %b %Y %H:%M} UTC")
        lines.append(f"Title: {r.title}")
        if r.excerpt:
            lines.append(f"Excerpt: {r.excerpt[:_MAX_EXCERPT_CHARS]}")
        lines.append("")
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------- Claude


class ClaudeSummarizer:
    def __init__(self, client: anthropic.AsyncAnthropic | None = None) -> None:
        settings = get_settings()
        self.model = settings.ai_summary_model
        self.effort = settings.ai_summary_effort
        self.client = client or anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key, timeout=60.0, max_retries=2
        )

    async def summarize(self, story: StoryIn) -> SummaryResult:
        try:
            response = await self.client.beta.messages.parse(
                model=self.model,
                max_tokens=4000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_prompt(story)}],
                output_format=SummaryOut,
                output_config={"effort": self.effort},
                # If a safety classifier declines, the API retries on a suitable model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.APIStatusError as e:
            log.warning("Claude returned %s: %s", e.status_code, e.message)
            error = f"{e.status_code}: {e.message}"[:500]
            return SummaryResult(status="failed", model=self.model, error=error)
        except anthropic.APIConnectionError as e:
            log.warning("could not reach Claude: %s", e)
            return SummaryResult(status="failed", model=self.model, error="connection error")
        except ValueError as e:  # output that doesn't match the schema (pydantic error)
            log.warning("Claude output did not parse: %s", e)
            return SummaryResult(status="failed", model=self.model, error="unparseable output")

        model = response.model or self.model
        usage = response.usage
        input_tokens = (
            usage.input_tokens
            + (usage.cache_read_input_tokens or 0)
            + (usage.cache_creation_input_tokens or 0)
        )
        result = SummaryResult(
            status="ok",
            model=model,
            input_tokens=input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=estimate_cost(model, input_tokens, usage.output_tokens),
        )
        if response.stop_reason == "refusal":
            result.status = "refused"
            details = response.stop_details
            result.error = f"refused: {getattr(details, 'category', None) or 'unspecified'}"
        elif response.stop_reason == "max_tokens" or response.parsed_output is None:
            result.status = "failed"
            result.error = f"no usable output (stop_reason={response.stop_reason})"
        else:
            result.output = response.parsed_output
        return result
