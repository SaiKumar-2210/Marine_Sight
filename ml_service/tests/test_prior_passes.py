"""The prior-pass search must stay bounded: 5 days, then a single most-recent fallback."""
from datetime import datetime, timedelta, timezone

from marinesight import vessels_sar

T = datetime(2026, 8, 7, 14, 22, tzinfo=timezone.utc)
BBOX = [56.9, 17.4, 57.1, 17.6]


def feature(dt: datetime) -> dict:
    return {"properties": {"datetime": dt.strftime("%Y-%m-%dT%H:%M:%SZ")}}


def fake_search(calls, by_window):
    """Stand-in for cdse.search that records each call and replays canned features."""
    def _search(collection, bbox, start, end, limit=100):
        calls.append({"start": start, "end": end, "limit": limit, "days": (end - start).days})
        return by_window(start, end)
    return _search


def install(monkeypatch, calls, by_window):
    import marinesight.cdse as cdse
    monkeypatch.setattr(cdse, "search", fake_search(calls, by_window))


def test_window_is_capped_at_five_days(monkeypatch):
    calls = []
    recent = [feature(T - timedelta(days=d)) for d in (1, 3, 4)]
    install(monkeypatch, calls, lambda s, e: [f for f in recent if s <= datetime.strptime(
        f["properties"]["datetime"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) <= e])

    prior = vessels_sar._prior_passes(BBOX, T, max_scenes=2)

    assert len(calls) == 1, "a populated window must not trigger the archive probe"
    assert vessels_sar.LOOKBACK_DAYS == 5
    assert calls[0]["start"] == T - timedelta(days=vessels_sar.LOOKBACK_DAYS)
    # Newest first, capped by max_scenes.
    assert prior == [(T - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     (T - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")]


def test_empty_window_falls_back_to_a_single_most_recent_scan(monkeypatch):
    calls = []
    old = [feature(T - timedelta(days=d)) for d in (9, 12, 20)]

    def by_window(start, end):
        if (T - end).days < 1:  # the 5-day window: nothing there
            return []
        return [f for f in old if start <= datetime.strptime(
            f["properties"]["datetime"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) <= end]

    install(monkeypatch, calls, by_window)
    prior = vessels_sar._prior_passes(BBOX, T, max_scenes=3)

    assert len(calls) == 2, "exactly one extra probe, never a day-by-day walk"
    assert calls[1]["start"] == T - timedelta(days=vessels_sar.ARCHIVE_PROBE_DAYS)
    assert calls[1]["end"] == T - timedelta(days=vessels_sar.LOOKBACK_DAYS)
    assert prior == [(T - timedelta(days=9)).strftime("%Y-%m-%dT%H:%M:%SZ")], "only the most recent one"


def test_nothing_at_all_gives_up_quietly(monkeypatch):
    calls = []
    install(monkeypatch, calls, lambda s, e: [])
    assert vessels_sar._prior_passes(BBOX, T, max_scenes=3) == []
    assert len(calls) == 2


def test_fetch_budget_bounds_the_work(monkeypatch):
    """Many scattered targets must not turn into an unbounded pile of Copernicus fetches."""
    calls, fetched = [], []
    install(monkeypatch, calls, lambda s, e: [feature(T - timedelta(days=2))])

    import marinesight.cdse as cdse

    def fake_fetch(bbox, when):
        fetched.append(when)
        raise cdse.CdseError("no data")  # the budget must count attempts, not successes

    monkeypatch.setattr(cdse, "fetch_s1_vv_db", fake_fetch)
    targets = [{"lon": 57.0 + i * 0.2, "lat": 17.5, "static": None} for i in range(20)]

    vessels_sar.static_targets(targets, T, max_scenes=2, max_fetches=4)

    assert len(fetched) <= 4
    assert all(t["static"] is False for t in targets), "unchecked targets are never called static"
    assert any("budget" in (t.get("staticCheck") or "") for t in targets)
