"""Profiles, scoring and the ranked feed through the HTTP API (needs Docker PostgreSQL/Redis)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.ingestion.normalize import canonical_url, normalise_title_for_hash, sha256
from app.models import Article, Source
from app.processing.service import process_all_pending
from app.scoring.service import score_stories
from tests.conftest import signup_body

pytestmark = pytest.mark.usefixtures("db")


async def sign_up(client: AsyncClient, email: str = "trader@example.com") -> dict:
    resp = await client.post("/api/auth/register", json=signup_body(email))
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def add_source(db, slug: str, homepage: str, **kw) -> uuid.UUID:
    values = {
        "slug": slug, "name": kw.pop("name", slug), "kind": "rss", "url": f"{homepage}/{slug}.xml",
        "homepage": homepage, "category": "press_india", "region": "IN",
        "licence_class": "headline_link", "authority": 3, "poll_interval_seconds": 60,
    } | kw  # fmt: skip
    async with db() as session, session.begin():
        source = Source(**values)
        session.add(source)
        await session.flush()
        return source.id


async def add_article(db, source_id: uuid.UUID, title: str, minutes_ago: int = 10) -> None:
    url = canonical_url(f"https://example.com/{uuid.uuid4().hex}")
    async with db() as session, session.begin():
        session.add(
            Article(
                source_id=source_id, guid=url, url=url, url_hash=sha256(url), title=title,
                title_hash=sha256(normalise_title_for_hash(title)),
                published_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
            )
        )  # fmt: skip


async def pipeline(db) -> None:
    """What the worker does after ingestion: process, then score touched stories."""
    result = await process_all_pending(db)
    await score_stories(db, list(result.touched_story_ids))


@pytest.fixture
async def news(db) -> None:
    rbi = await add_source(
        db, "rbi-press-releases", "https://www.rbi.org.in", name="RBI Press Releases",
        category="official_india", licence_class="official", authority=5,
    )  # fmt: skip
    mint = await add_source(db, "mint-markets", "https://www.livemint.com", name="Mint - Markets")
    et = await add_source(
        db, "et-markets", "https://economictimes.indiatimes.com", name="ET Markets"
    )
    bbc = await add_source(
        db, "bbc-world", "https://www.bbc.com", name="BBC World", region="GLOBAL", authority=4
    )
    await add_article(
        db, rbi, "RBI keeps repo rate unchanged at 5.5%, maintains neutral stance", 30
    )
    await add_article(db, mint, "RBI holds repo rate at 5.5% as expected; rupee steady", 20)
    await add_article(db, rbi, "Auction of 91-Day, 182-Day and 364-Day Treasury Bills", 50)
    await add_article(db, et, "Brent crude jumps 4% after supply disruption in the Red Sea", 40)
    await add_article(db, bbc, "Powerful magnitude 7.7 earthquake hits Panama", 15)
    await add_article(db, mint, "IPL auction: record bid for Indian all-rounder", 5)
    await pipeline(db)


async def test_new_user_feed_is_ranked_with_reasons(api, news) -> None:
    client = api()
    headers = await sign_up(client)

    resp = await client.get("/api/feed", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    titles = [i["title"] for i in body["items"]]

    top = body["items"][0]
    assert top["title"].startswith("RBI keeps repo rate unchanged")  # official headline leads
    assert (top["score"], top["tier"], top["pinned"], top["alert"]) == (95, "critical", True, True)
    assert top["source_count"] == 2 and len(top["articles"]) == 2
    assert top["reasons"][0]["label"].startswith("Pinned: Reserve Bank of India")

    assert any("Brent" in t for t in titles)
    assert not any("earthquake" in t or "IPL" in t for t in titles)  # irrelevant: hidden
    scores = [i["score"] for i in body["items"]]
    assert scores == sorted(scores, reverse=True)
    assert all(s >= body["profile"]["show_threshold"] for s in scores)


async def test_feed_filters_and_sorting(api, news) -> None:
    client = api()
    headers = await sign_up(client)

    latest = (await client.get("/api/feed?sort=latest", headers=headers)).json()["items"]
    times = [i["first_published_at"] for i in latest]
    assert times == sorted(times, reverse=True)

    critical = (await client.get("/api/feed?tier=critical", headers=headers)).json()
    assert {i["tier"] for i in critical["items"]} == {"critical"}

    no_routine = (await client.get("/api/feed?include_routine=false", headers=headers)).json()
    assert not any(i["is_routine"] for i in no_routine["items"])

    energy = (await client.get("/api/feed?topic=energy", headers=headers)).json()
    assert energy["items"] and all("energy" in i["topics"] for i in energy["items"])

    page = (await client.get("/api/feed?limit=1", headers=headers)).json()
    assert len(page["items"]) == 1 and page["next_offset"] == 1 and page["total"] >= 2


async def test_story_arriving_after_signup_is_scored_into_the_feed(api, db, news) -> None:
    client = api()
    headers = await sign_up(client)
    before = (await client.get("/api/feed", headers=headers)).json()["total"]

    sebi = await add_source(
        db, "sebi", "https://www.sebi.gov.in", name="SEBI", category="official_india",
        licence_class="official", authority=5,
    )  # fmt: skip
    await add_article(db, sebi, "SEBI tightens F&O rules for retail traders, new framework", 1)
    await pipeline(db)

    feed = (await client.get("/api/feed", headers=headers)).json()
    assert feed["total"] == before + 1
    assert any("SEBI tightens" in i["title"] for i in feed["items"])


async def test_editing_a_profile_rescores_immediately(api, news) -> None:
    client = api()
    headers = await sign_up(client)
    [summary] = (await client.get("/api/profiles", headers=headers)).json()
    profile = (await client.get(f"/api/profiles/{summary['id']}", headers=headers)).json()
    assert any(r["mode"] == "pin" and r["entity"] == "rbi" for r in profile["rules"])

    # Mute crude oil for a week.
    week = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    rules = [{k: r[k] for k in ("kind", "value", "mode", "weight", "entity", "min_score")}
             for r in profile["rules"]]  # fmt: skip
    rules.append({"kind": "entity", "value": "brent", "mode": "exclude", "expires_at": week})
    put = await client.put(
        f"/api/profiles/{summary['id']}",
        headers=headers,
        json={"name": "Trader (no crude)", "show_threshold": 50, "alert_threshold": 80,
              "rules": rules},
    )  # fmt: skip
    assert put.status_code == 200, put.text
    assert put.json()["version"] == 2

    titles = [i["title"] for i in (await client.get("/api/feed", headers=headers)).json()["items"]]
    assert not any("Brent" in t for t in titles)
    assert any("repo rate" in t for t in titles)


async def test_blank_profile_and_validation(api, news) -> None:
    client = api()
    headers = await sign_up(client)

    bad = await client.post(
        "/api/profiles",
        headers=headers,
        json={"name": "Bad", "rules": [{"kind": "entity", "value": "not_a_company"}]},
    )
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_rule"

    pin_without_score = await client.post(
        "/api/profiles",
        headers=headers,
        json={"name": "Bad", "rules": [{"kind": "topic", "value": "energy", "mode": "pin"}]},
    )
    assert pin_without_score.status_code == 422

    thresholds = await client.post(
        "/api/profiles",
        headers=headers,
        json={"name": "Bad", "show_threshold": 70, "alert_threshold": 60, "rules": []},
    )
    assert thresholds.status_code == 422

    energy = await client.post(
        "/api/profiles",
        headers=headers,
        json={"name": "Energy desk", "show_threshold": 40,
              "rules": [{"kind": "topic", "value": "energy", "weight": 9},
                        {"kind": "keyword", "value": "Red Sea", "weight": 6}]},
    )  # fmt: skip
    assert energy.status_code == 201
    feed = (await client.get(f"/api/feed?profile_id={energy.json()['id']}", headers=headers)).json()
    assert [i["title"] for i in feed["items"]] == [
        "Brent crude jumps 4% after supply disruption in the Red Sea"
    ]
    labels = [r["label"] for r in feed["items"][0]["reasons"]]
    assert "Keyword: Red Sea" in labels and "Topic: Crude oil and energy" in labels


async def test_profile_limit_and_last_profile_protection(api, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "max_profiles_per_user", 2)
    client = api()
    headers = await sign_up(client)
    [first] = (await client.get("/api/profiles", headers=headers)).json()

    second = await client.post(
        "/api/profiles", headers=headers, json={"template": "indian_markets_trader", "name": "Copy"}
    )
    assert second.status_code == 201
    third = await client.post("/api/profiles", headers=headers, json={"name": "Third", "rules": []})
    assert third.status_code == 409 and third.json()["detail"]["code"] == "profile_limit"

    assert (
        await client.delete(f"/api/profiles/{second.json()['id']}", headers=headers)
    ).status_code == 204
    last = await client.delete(f"/api/profiles/{first['id']}", headers=headers)
    assert last.status_code == 409 and last.json()["detail"]["code"] == "last_profile"


async def test_other_users_profiles_are_invisible(api, news) -> None:
    alice, bob = api(), api()
    alice_headers = await sign_up(alice, "alice@example.com")
    bob_headers = await sign_up(bob, "bob@example.com")
    [alice_profile] = (await alice.get("/api/profiles", headers=alice_headers)).json()

    pid = alice_profile["id"]
    assert (await bob.get(f"/api/profiles/{pid}", headers=bob_headers)).status_code == 404
    assert (await bob.get(f"/api/feed?profile_id={pid}", headers=bob_headers)).status_code == 404
    assert (await bob.delete(f"/api/profiles/{pid}", headers=bob_headers)).status_code == 404


async def test_preview_scores_without_saving(api, news) -> None:
    client = api()
    headers = await sign_up(client)
    resp = await client.post(
        "/api/profiles/preview",
        headers=headers,
        json={"rules": [{"kind": "entity", "value": "rbi", "weight": 9}], "show_threshold": 40},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["shown"] == 2  # the decision and the T-bill auction
    assert body["stories"][0]["score"] > body["stories"][1]["score"]
    assert body["by_tier"]


async def test_meta_endpoints_are_public(api, news) -> None:
    client = api()
    taxonomy = (await client.get("/api/meta/taxonomy")).json()
    assert {"rbi", "reliance"} <= {e["slug"] for e in taxonomy["entities"]}
    templates = (await client.get("/api/meta/templates")).json()
    assert templates[0]["slug"] == "indian_markets_trader"
    sources = (await client.get("/api/meta/sources")).json()
    assert "rbi-press-releases" in {s["slug"] for s in sources}


async def test_feed_filters_by_source(api, news) -> None:
    client = api()
    headers = await sign_up(client)
    feed = (await client.get("/api/feed", params={"since_hours": 168}, headers=headers)).json()
    rbi_story = next(i for i in feed["items"] if i["title"].startswith("RBI keeps repo rate"))
    # Reported by both RBI and Mint: the story lists every source that carried it.
    assert set(rbi_story["source_slugs"]) == {"rbi-press-releases", "mint-markets"}

    only_et = (
        await client.get(
            "/api/feed", params={"since_hours": 168, "source": "et-markets"}, headers=headers
        )
    ).json()
    assert only_et["total"] >= 1
    assert all("et-markets" in i["source_slugs"] for i in only_et["items"])
    nothing = (
        await client.get("/api/feed", params={"source": "no-such-source"}, headers=headers)
    ).json()
    assert nothing["total"] == 0
