"""
Adaptive Paper Trader Challenge
===============================
A beginner-friendly fake-money stock game with a live-updating, news-aware desktop UI.

Rules:
- Start with $20 in fake money.
- A weighted +1% portfolio move equals +1 point.
- A weighted -1% portfolio move equals -1 point.
- Reach 100 points to win.
- The bot can go all-in or split between several companies.

This app never places real trades. Market prices and ticker-related headlines
come from Yahoo Finance public endpoints and may be delayed, incomplete,
incorrectly matched, or unavailable.
"""

from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import os
import queue
import random
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import asdict, dataclass, field
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable



APP_NAME = "Adaptive Paper Trader"
APP_VERSION = "6.1-cloud-daily-reroll"
STARTING_CASH = 20.0
GOAL_POINTS = 100.0
DEFAULT_REFRESH_SECONDS = 60
NEWS_REFRESH_SECONDS = 600
NEWS_LOOKBACK_DAYS = 2
ROUND_CANDIDATE_POOL_SIZE = 18
NEWS_SCORE_WEIGHT = 0.75
LEGACY_DEFAULT_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL",
    "META", "TSLA", "JPM", "XOM", "SPY",
]
DEFAULT_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM",
    "XOM", "UNH", "JNJ", "V", "PG", "MA", "HD", "COST", "ABBV", "BAC",
    "KO", "PEP", "CRM", "NFLX", "AMD", "INTC", "CSCO", "ORCL", "QCOM",
    "ADBE", "DIS", "MCD", "NKE", "WMT", "TGT", "SBUX", "CAT", "BA",
    "GE", "IBM", "GS", "MS", "C", "CVX", "COP", "LLY", "MRK", "PFE",
    "TMO", "AVGO", "TXN", "AMAT", "NOW", "UBER", "ABNB", "F", "GM",
]
HISTORY_FIELDS = [
    "market_date", "timestamp", "strategy", "cash_before", "cash_after",
    "point_change", "banked_points", "companies", "allocation",
    "news_summary", "news_snapshot",
]
STRATEGIES = ("trend", "dip", "steady", "breakout")
STRATEGY_LABELS = {
    "trend": "Trend Rider",
    "dip": "Dip Hunter",
    "steady": "Steady Picker",
    "breakout": "Breakout Watcher",
}
STRATEGY_EXPLANATIONS = {
    "trend": "The bot favors companies already moving upward.",
    "dip": "The bot looks for solid companies that recently pulled back.",
    "steady": "The bot favors smoother price movement and lower volatility.",
    "breakout": "The bot looks for prices pushing above recent averages.",
}

BG = "#0f172a"
CARD = "#172033"
CARD_ALT = "#1e293b"
TEXT = "#f8fafc"
MUTED = "#94a3b8"
ACCENT = "#38bdf8"
GREEN = "#22c55e"
RED = "#ef4444"
GOLD = "#f59e0b"
BORDER = "#334155"


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> int:
    first = datetime(year, month, 1)
    shift = (weekday - first.weekday()) % 7
    return 1 + shift + 7 * (occurrence - 1)


def eastern_datetime_from_timestamp(timestamp: float) -> datetime:
    """Return US Eastern time without requiring the optional Windows tzdata package."""
    utc_value = datetime.fromtimestamp(timestamp, timezone.utc)
    year = utc_value.year
    march_sunday = _nth_weekday(year, 3, 6, 2)
    november_sunday = _nth_weekday(year, 11, 6, 1)
    dst_start_utc = datetime(year, 3, march_sunday, 7, 0, tzinfo=timezone.utc)
    dst_end_utc = datetime(year, 11, november_sunday, 6, 0, tzinfo=timezone.utc)
    offset_hours = -4 if dst_start_utc <= utc_value < dst_end_utc else -5
    return utc_value.astimezone(timezone(timedelta(hours=offset_hours)))


def app_data_dir() -> Path:
    """Repository data: persists across cloud workflow executions via Git commits."""
    folder = Path(__file__).resolve().parent / "data"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


DATA_DIR = app_data_dir()
STATE_FILE = DATA_DIR / "bot_state.json"
HISTORY_FILE = DATA_DIR / "paper_history.csv"


@dataclass
class BotState:
    cash_at_round_start: float = STARTING_CASH
    banked_points: float = 0.0
    goal: float = GOAL_POINTS
    tickers: list[str] = field(default_factory=lambda: DEFAULT_TICKERS.copy())
    max_positions: int = 4
    strategy_scores: dict[str, float] = field(
        default_factory=lambda: {name: 0.0 for name in STRATEGIES}
    )
    active_strategy: str | None = None
    allocation: dict[str, float] = field(default_factory=dict)
    entry_prices: dict[str, float] = field(default_factory=dict)
    round_market_date: str | None = None
    round_started_at: str | None = None
    rounds_completed: int = 0
    last_round_tickers: list[str] = field(default_factory=list)
    last_reroll_market_date: str | None = None  # One fresh allocation per market date.
    candidate_pool_size: int = ROUND_CANDIDATE_POOL_SIZE
    refresh_seconds: int = DEFAULT_REFRESH_SECONDS
    auto_refresh: bool = True
    news_enabled: bool = True
    news_learning_score: float = 0.0
    news_last_checked_at: str | None = None
    news_signals: dict[str, dict[str, Any]] = field(default_factory=dict)
    entry_news_signals: dict[str, dict[str, Any]] = field(default_factory=dict)
    selection_scores: dict[str, dict[str, float]] = field(default_factory=dict)
    data_version: int = 5

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BotState":
        defaults = asdict(cls())
        defaults.update(raw)
        state = cls(**defaults)
        state.tickers = clean_tickers(state.tickers) or DEFAULT_TICKERS.copy()
        if set(state.tickers) == set(LEGACY_DEFAULT_TICKERS) and len(state.tickers) == len(LEGACY_DEFAULT_TICKERS):
            state.tickers = DEFAULT_TICKERS.copy()
        state.last_round_tickers = clean_tickers(state.last_round_tickers)
        # A saved active position already used up that market day's reroll.
        if not state.last_reroll_market_date and state.round_market_date and state.allocation:
            state.last_reroll_market_date = state.round_market_date
        state.max_positions = max(1, min(int(state.max_positions), len(state.tickers), 8))
        state.candidate_pool_size = max(8, min(int(state.candidate_pool_size), len(state.tickers), 30))
        state.refresh_seconds = max(30, min(int(state.refresh_seconds), 900))
        try:
            state.news_learning_score = max(-1.0, min(1.0, float(state.news_learning_score)))
        except (TypeError, ValueError):
            state.news_learning_score = 0.0
        if not isinstance(state.news_signals, dict):
            state.news_signals = {}
        if not isinstance(state.entry_news_signals, dict):
            state.entry_news_signals = {}
        if not isinstance(state.selection_scores, dict):
            state.selection_scores = {}
        state.data_version = 5
        for strategy in STRATEGIES:
            state.strategy_scores.setdefault(strategy, 0.0)
        return state


@dataclass
class Quote:
    ticker: str
    price: float
    market_timestamp: int
    market_date: str
    currency: str = "USD"


@dataclass
class Snapshot:
    prices: dict[str, float]
    market_dates: dict[str, str]
    portfolio_return: float
    round_points: float
    total_points: float
    live_cash: float
    fetched_at: datetime


@dataclass
class NewsItem:
    ticker: str
    title: str
    publisher: str
    published_at: str
    published_timestamp: int
    link: str
    score: float


@dataclass
class NewsSignal:
    ticker: str
    score: float
    label: str
    headline_count: int
    top_headline: str
    publisher: str
    published_at: str
    link: str
    items: list[NewsItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "score": self.score,
            "label": self.label,
            "headline_count": self.headline_count,
            "top_headline": self.top_headline,
            "publisher": self.publisher,
            "published_at": self.published_at,
            "link": self.link,
            "items": [asdict(item) for item in self.items],
        }


def clean_tickers(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        ticker = str(value).strip().upper()
        if not ticker:
            continue
        allowed = all(ch.isalnum() or ch in ".-^=" for ch in ticker)
        if allowed and ticker not in result:
            result.append(ticker)
    return result


def save_state(state: BotState) -> None:
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)


def load_state() -> BotState | None:
    if not STATE_FILE.exists():
        return None
    try:
        return BotState.from_dict(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _companies_from_allocation(raw: str) -> str:
    try:
        allocation = json.loads(raw or "{}")
        if isinstance(allocation, dict):
            return ", ".join(clean_tickers(allocation.keys()))
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return ""


def ensure_history_schema() -> None:
    if not HISTORY_FILE.exists():
        return
    try:
        with HISTORY_FILE.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            current_fields = reader.fieldnames or []
        if current_fields == HISTORY_FIELDS:
            return
        normalized: list[dict[str, str]] = []
        for row in rows:
            timestamp = row.get("timestamp", "")
            market_date = row.get("market_date", "") or timestamp[:10]
            allocation = row.get("allocation", "")
            normalized.append(
                {
                    "market_date": market_date,
                    "timestamp": timestamp,
                    "strategy": row.get("strategy", ""),
                    "cash_before": row.get("cash_before", ""),
                    "cash_after": row.get("cash_after", ""),
                    "point_change": row.get("point_change", ""),
                    "banked_points": row.get("banked_points", ""),
                    "companies": row.get("companies", "") or _companies_from_allocation(allocation),
                    "allocation": allocation,
                    "news_summary": row.get("news_summary", ""),
                    "news_snapshot": row.get("news_snapshot", ""),
                }
            )
        temp = HISTORY_FILE.with_suffix(".migrating.csv")
        with temp.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=HISTORY_FIELDS)
            writer.writeheader()
            writer.writerows(normalized)
        temp.replace(HISTORY_FILE)
    except OSError:
        return


def append_history(row: dict[str, Any]) -> None:
    ensure_history_schema()
    new_file = not HISTORY_FILE.exists()
    with HISTORY_FILE.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow({name: row.get(name, "") for name in HISTORY_FIELDS})


def read_history(limit: int = 80) -> list[dict[str, str]]:
    if not HISTORY_FILE.exists():
        return []
    ensure_history_schema()
    try:
        with HISTORY_FILE.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        return rows[-limit:]
    except OSError:
        return []


def read_daily_history(limit: int = 365) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in read_history(limit=5000):
        date_text = row.get("market_date", "") or row.get("timestamp", "")[:10] or "Unknown"
        entry = grouped.setdefault(
            date_text,
            {
                "market_date": date_text,
                "point_change": 0.0,
                "banked_points": 0.0,
                "cash_before": None,
                "cash_after": None,
                "cash_change": 0.0,
                "rounds": 0,
                "strategies": [],
                "companies": [],
                "news_summaries": [],
            },
        )
        try:
            entry["point_change"] += float(row.get("point_change", "0") or 0)
        except ValueError:
            pass
        try:
            entry["banked_points"] = float(row.get("banked_points", "0") or 0)
        except ValueError:
            pass
        try:
            row_cash_before = float(row.get("cash_before", "") or 0)
            if entry["cash_before"] is None:
                entry["cash_before"] = row_cash_before
        except ValueError:
            pass
        try:
            entry["cash_after"] = float(row.get("cash_after", "") or 0)
        except ValueError:
            pass
        entry["rounds"] += 1
        strategy = row.get("strategy", "")
        if strategy and strategy not in entry["strategies"]:
            entry["strategies"].append(strategy)
        companies = clean_tickers((row.get("companies", "") or _companies_from_allocation(row.get("allocation", ""))).split(","))
        for company in companies:
            if company not in entry["companies"]:
                entry["companies"].append(company)
        news_summary = (row.get("news_summary", "") or "").strip()
        if news_summary and news_summary not in entry["news_summaries"]:
            entry["news_summaries"].append(news_summary)

    daily_rows = list(grouped.values())
    for entry in daily_rows:
        before = entry.get("cash_before")
        after = entry.get("cash_after")
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            entry["cash_change"] = after - before
        else:
            entry["cash_change"] = 0.0
            if after is None:
                entry["cash_after"] = 0.0
            if before is None:
                entry["cash_before"] = 0.0
    return daily_rows[-limit:]


class YahooChartClient:
    """Small, dependency-free client for Yahoo's public chart endpoint."""

    BASE = "https://query1.finance.yahoo.com/v8/finance/chart/{}"

    def __init__(self, timeout: int = 12) -> None:
        self.timeout = timeout

    def _request(self, ticker: str, data_range: str, interval: str) -> dict[str, Any]:
        safe_ticker = urllib.parse.quote(ticker, safe="")
        params = urllib.parse.urlencode(
            {
                "range": data_range,
                "interval": interval,
                "includePrePost": "false",
                "events": "div,splits",
            }
        )
        request = urllib.request.Request(
            f"{self.BASE.format(safe_ticker)}?{params}",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124 Safari/537.36"
                ),
                "Accept": "application/json,text/plain,*/*",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Price service returned HTTP {exc.code} for {ticker}.") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach the price service for {ticker}.") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"The price service returned unreadable data for {ticker}.") from exc

        chart = payload.get("chart", {})
        error = chart.get("error")
        result = chart.get("result")
        if error or not result:
            description = (error or {}).get("description") or "No market data was returned."
            raise RuntimeError(f"{ticker}: {description}")
        return result[0]

    def latest_quote(self, ticker: str) -> Quote:
        result = self._request(ticker, "1d", "1m")
        meta = result.get("meta", {})
        timestamps = result.get("timestamp") or []
        quote_block = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        closes = quote_block.get("close") or []

        price = meta.get("regularMarketPrice")
        timestamp = meta.get("regularMarketTime")

        if price is None:
            for ts, close in reversed(list(zip(timestamps, closes))):
                if close is not None:
                    price = close
                    timestamp = ts
                    break

        if price is None or timestamp is None:
            # A daily fallback is useful outside market hours and for thin symbols.
            result = self._request(ticker, "5d", "1d")
            meta = result.get("meta", {})
            timestamps = result.get("timestamp") or []
            quote_block = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            closes = quote_block.get("close") or []
            for ts, close in reversed(list(zip(timestamps, closes))):
                if close is not None:
                    price = close
                    timestamp = ts
                    break

        if price is None or timestamp is None or float(price) <= 0:
            raise RuntimeError(f"No usable price was found for {ticker}.")

        offset_seconds = meta.get("gmtoffset")
        try:
            if offset_seconds is not None:
                market_zone = timezone(timedelta(seconds=int(offset_seconds)))
                date_text = datetime.fromtimestamp(int(timestamp), market_zone).date().isoformat()
            else:
                date_text = eastern_datetime_from_timestamp(int(timestamp)).date().isoformat()
        except Exception:
            date_text = eastern_datetime_from_timestamp(int(timestamp)).date().isoformat()

        return Quote(
            ticker=ticker,
            price=float(price),
            market_timestamp=int(timestamp),
            market_date=date_text,
            currency=str(meta.get("currency") or "USD"),
        )

    def daily_closes(self, ticker: str, data_range: str = "1y") -> list[tuple[int, float]]:
        result = self._request(ticker, data_range, "1d")
        timestamps = result.get("timestamp") or []
        quote_block = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        closes = quote_block.get("close") or []
        series = [
            (int(ts), float(close))
            for ts, close in zip(timestamps, closes)
            if close is not None and float(close) > 0
        ]
        if len(series) < 55:
            raise RuntimeError(f"{ticker} needs more usable price history.")
        return series

    def recent_news(self, ticker: str, count: int = 10) -> list[NewsItem]:
        """Fetch recent ticker-related headlines from Yahoo's search/news feed."""
        params = urllib.parse.urlencode(
            {
                "q": ticker,
                "quotesCount": 1,
                "newsCount": max(1, min(count, 20)),
                "listsCount": 0,
                "enableFuzzyQuery": "false",
            }
        )
        request = urllib.request.Request(
            f"https://query1.finance.yahoo.com/v1/finance/search?{params}",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124 Safari/537.36"
                ),
                "Accept": "application/json,text/plain,*/*",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"News service returned HTTP {exc.code} for {ticker}.") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach the news service for {ticker}.") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"The news service returned unreadable data for {ticker}.") from exc

        eastern_now = eastern_datetime_from_timestamp(time.time())
        earliest_date = eastern_now.date() - timedelta(days=NEWS_LOOKBACK_DAYS - 1)
        items: list[NewsItem] = []
        seen_titles: set[str] = set()

        for raw in payload.get("news", []) or []:
            title = str(raw.get("title") or "").strip()
            if not title or title.lower() in seen_titles:
                continue

            related = clean_tickers(raw.get("relatedTickers") or [])
            if related and ticker not in related:
                continue

            try:
                published_timestamp = int(raw.get("providerPublishTime") or 0)
            except (TypeError, ValueError):
                continue
            if published_timestamp <= 0:
                continue

            published_eastern = eastern_datetime_from_timestamp(published_timestamp)
            if published_eastern.date() < earliest_date or published_eastern.date() > eastern_now.date():
                continue

            link = str(raw.get("link") or "").strip()
            if not link:
                canonical = raw.get("canonicalUrl") or {}
                if isinstance(canonical, dict):
                    link = str(canonical.get("url") or "").strip()
            if link and not link.lower().startswith(("http://", "https://")):
                link = ""

            title_key = title.lower()
            seen_titles.add(title_key)
            items.append(
                NewsItem(
                    ticker=ticker,
                    title=title,
                    publisher=str(raw.get("publisher") or "Unknown source"),
                    published_at=published_eastern.strftime("%b %d, %I:%M %p").replace(" 0", " "),
                    published_timestamp=published_timestamp,
                    link=link,
                    score=score_headline(title),
                )
            )

        items.sort(key=lambda item: item.published_timestamp, reverse=True)
        return items


POSITIVE_NEWS_PHRASES = {
    "raises guidance": 2.5,
    "raised guidance": 2.5,
    "beats estimates": 2.2,
    "beat estimates": 2.2,
    "earnings beat": 2.0,
    "record revenue": 2.0,
    "record profit": 2.0,
    "wins contract": 2.0,
    "won contract": 2.0,
    "receives approval": 2.2,
    "approved by": 1.8,
    "production begins": 1.8,
    "begins production": 1.8,
    "starts production": 1.8,
    "expands production": 1.6,
    "new factory": 1.5,
    "new plant": 1.5,
    "launches new": 1.3,
    "unveils new": 1.2,
    "strategic partnership": 1.3,
    "share buyback": 1.4,
    "increases dividend": 1.4,
    "price target raised": 1.0,
    "upgraded to": 1.2,
}
NEGATIVE_NEWS_PHRASES = {
    "cuts guidance": -2.5,
    "cut guidance": -2.5,
    "misses estimates": -2.2,
    "missed estimates": -2.2,
    "earnings miss": -2.0,
    "profit warning": -2.2,
    "product recall": -2.1,
    "recalls vehicles": -2.0,
    "production halt": -2.0,
    "halts production": -2.0,
    "production delay": -1.8,
    "delays launch": -1.6,
    "data breach": -2.0,
    "cyberattack": -1.8,
    "regulatory probe": -1.9,
    "under investigation": -1.8,
    "antitrust lawsuit": -1.7,
    "files for bankruptcy": -3.0,
    "bankruptcy filing": -3.0,
    "cuts dividend": -1.8,
    "price target cut": -1.0,
    "downgraded to": -1.2,
}
POSITIVE_NEWS_WORDS = {
    "approval": 0.55, "approved": 0.55, "growth": 0.35, "profit": 0.35,
    "record": 0.45, "expands": 0.35, "expansion": 0.35, "launch": 0.30,
    "launches": 0.30, "contract": 0.30, "partnership": 0.30, "upgrade": 0.35,
    "upgraded": 0.35, "surges": 0.40, "rises": 0.20, "boost": 0.25,
    "manufacturing": 0.20, "production": 0.20, "innovation": 0.20,
}
NEGATIVE_NEWS_WORDS = {
    "recall": -0.60, "lawsuit": -0.55, "probe": -0.55, "investigation": -0.50,
    "downgrade": -0.40, "downgraded": -0.40, "miss": -0.35, "misses": -0.35,
    "delay": -0.35, "delays": -0.35, "declines": -0.25, "falls": -0.25,
    "layoffs": -0.45, "fraud": -0.75, "bankruptcy": -0.90, "breach": -0.55,
    "warning": -0.45, "tariff": -0.20, "fine": -0.35, "fined": -0.35,
}
SPECULATIVE_WORDS = {"could", "might", "may", "rumor", "reportedly", "opinion", "prediction", "why"}


def score_headline(title: str) -> float:
    """Transparent, intentionally simple headline score in the range -1 to +1."""
    lowered = " ".join(title.lower().replace("’", "'").split())
    raw_score = 0.0

    for phrase, value in POSITIVE_NEWS_PHRASES.items():
        if phrase in lowered:
            raw_score += value
    for phrase, value in NEGATIVE_NEWS_PHRASES.items():
        if phrase in lowered:
            raw_score += value

    words = {word.strip(".,:;!?()[]{}\"'") for word in lowered.split()}
    raw_score += sum(value for word, value in POSITIVE_NEWS_WORDS.items() if word in words)
    raw_score += sum(value for word, value in NEGATIVE_NEWS_WORDS.items() if word in words)

    if words.intersection(SPECULATIVE_WORDS):
        raw_score *= 0.55
    if "not " in lowered or "no " in lowered:
        raw_score *= 0.75

    return max(-1.0, min(1.0, math.tanh(raw_score / 2.2)))


def news_label(score: float) -> str:
    if score >= 0.45:
        return "Strong positive"
    if score >= 0.12:
        return "Positive"
    if score <= -0.45:
        return "Strong negative"
    if score <= -0.12:
        return "Negative"
    return "Neutral"


def summarize_news(ticker: str, items: list[NewsItem]) -> NewsSignal:
    if not items:
        return NewsSignal(
            ticker=ticker,
            score=0.0,
            label="No recent news",
            headline_count=0,
            top_headline="No company-specific headline found from today or yesterday.",
            publisher="—",
            published_at="—",
            link="",
            items=[],
        )

    eastern_today = eastern_datetime_from_timestamp(time.time()).date()
    weighted_total = 0.0
    total_weight = 0.0
    for item in items:
        item_date = eastern_datetime_from_timestamp(item.published_timestamp).date()
        recency_weight = 1.0 if item_date == eastern_today else 0.72
        impact_weight = 0.65 + 0.70 * abs(item.score)
        weight = recency_weight * impact_weight
        weighted_total += item.score * weight
        total_weight += weight

    combined = weighted_total / total_weight if total_weight else 0.0
    combined *= min(1.0, 0.55 + 0.15 * len(items))
    combined = max(-1.0, min(1.0, combined))
    top = max(items, key=lambda item: (abs(item.score), item.published_timestamp))
    return NewsSignal(
        ticker=ticker,
        score=combined,
        label=news_label(combined),
        headline_count=len(items),
        top_headline=top.title,
        publisher=top.publisher,
        published_at=top.published_at,
        link=top.link,
        items=items[:8],
    )


def fetch_news_parallel(
    client: YahooChartClient,
    tickers: Iterable[str],
) -> tuple[dict[str, NewsSignal], list[str]]:
    symbols = clean_tickers(tickers)
    signals: dict[str, NewsSignal] = {}
    failures: list[str] = []
    if not symbols:
        return signals, failures

    with ThreadPoolExecutor(max_workers=min(6, len(symbols))) as executor:
        jobs = {executor.submit(client.recent_news, ticker, 10): ticker for ticker in symbols}
        for future in as_completed(jobs):
            ticker = jobs[future]
            try:
                signals[ticker] = summarize_news(ticker, future.result())
            except Exception:
                failures.append(ticker)
                signals[ticker] = summarize_news(ticker, [])
    return signals, failures


def news_check_due(last_checked_at: str | None) -> bool:
    if not last_checked_at:
        return True
    try:
        last = datetime.fromisoformat(last_checked_at)
        if last.tzinfo is None:
            last = last.astimezone()
        return (datetime.now().astimezone() - last).total_seconds() >= NEWS_REFRESH_SECONDS
    except (TypeError, ValueError):
        return True


def fetch_quotes_parallel(client: YahooChartClient, tickers: Iterable[str]) -> dict[str, Quote]:
    symbols = list(tickers)
    if not symbols:
        return {}
    quotes: dict[str, Quote] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as executor:
        jobs = {executor.submit(client.latest_quote, ticker): ticker for ticker in symbols}
        for future in as_completed(jobs):
            ticker = jobs[future]
            try:
                quotes[ticker] = future.result()
            except Exception as exc:
                errors.append(f"{ticker}: {exc}")
    if errors:
        raise RuntimeError("Could not update all selected companies. " + " | ".join(errors[:3]))
    return quotes


def fetch_history_parallel(
    client: YahooChartClient, tickers: Iterable[str], data_range: str = "1y"
) -> tuple[dict[str, list[tuple[int, float]]], list[str]]:
    symbols = list(tickers)
    history: dict[str, list[tuple[int, float]]] = {}
    failures: list[str] = []
    if not symbols:
        return history, failures
    with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as executor:
        jobs = {
            executor.submit(client.daily_closes, ticker, data_range): ticker
            for ticker in symbols
        }
        for future in as_completed(jobs):
            ticker = jobs[future]
            try:
                history[ticker] = future.result()
            except Exception:
                failures.append(ticker)
    return history, failures


# ---------- Adaptive engine ----------

def pct_change(values: list[float], periods: int) -> float:
    if len(values) <= periods or values[-periods - 1] == 0:
        return 0.0
    return values[-1] / values[-periods - 1] - 1.0


def simple_rsi(values: list[float], window: int = 14) -> float:
    if len(values) < window + 1:
        return 50.0
    changes = [values[i] - values[i - 1] for i in range(len(values) - window, len(values))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains) / window
    avg_loss = sum(losses) / window
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    relative_strength = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def build_features(history: dict[str, list[tuple[int, float]]]) -> dict[str, dict[str, float]]:
    features: dict[str, dict[str, float]] = {}
    for ticker, series in history.items():
        values = [close for _, close in series]
        if len(values) < 55:
            continue
        returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values)) if values[i - 1] > 0]
        sma20 = sum(values[-20:]) / 20
        sma50 = sum(values[-50:]) / 50
        vol20 = statistics.pstdev(returns[-20:]) if len(returns) >= 20 else 0.0
        features[ticker] = {
            "close": values[-1],
            "mom5": pct_change(values, 5),
            "mom20": pct_change(values, 20),
            "vol20": vol20,
            "rsi14": simple_rsi(values, 14),
            "vs_sma20": values[-1] / sma20 - 1.0 if sma20 else 0.0,
            "vs_sma50": values[-1] / sma50 - 1.0 if sma50 else 0.0,
        }
    return features


def zscores(features: dict[str, dict[str, float]], key: str) -> dict[str, float]:
    values = [row[key] for row in features.values()]
    if not values:
        return {}
    mean = statistics.fmean(values)
    deviation = statistics.pstdev(values)
    if deviation < 1e-12:
        return {ticker: 0.0 for ticker in features}
    return {ticker: (row[key] - mean) / deviation for ticker, row in features.items()}


def strategy_stock_scores(features: dict[str, dict[str, float]], strategy: str) -> dict[str, float]:
    mom5 = zscores(features, "mom5")
    mom20 = zscores(features, "mom20")
    vol20 = zscores(features, "vol20")
    rsi_pullback_raw = {
        ticker: (50.0 - row["rsi14"]) / 50.0 for ticker, row in features.items()
    }
    pullback_features = {
        ticker: {"pullback": value} for ticker, value in rsi_pullback_raw.items()
    }
    rsi_centered = zscores(pullback_features, "pullback")
    sma20 = zscores(features, "vs_sma20")
    sma50 = zscores(features, "vs_sma50")

    scores: dict[str, float] = {}
    for ticker in features:
        if strategy == "trend":
            score = 0.45 * mom5[ticker] + 0.45 * mom20[ticker] + 0.10 * sma50[ticker] - 0.15 * vol20[ticker]
        elif strategy == "dip":
            score = 0.50 * rsi_centered[ticker] - 0.35 * mom5[ticker] + 0.20 * mom20[ticker] - 0.10 * vol20[ticker]
        elif strategy == "steady":
            score = 0.45 * mom20[ticker] + 0.25 * sma50[ticker] - 0.60 * vol20[ticker]
        elif strategy == "breakout":
            score = 0.45 * sma20[ticker] + 0.35 * mom5[ticker] + 0.25 * mom20[ticker] + 0.10 * vol20[ticker]
        else:
            raise ValueError(f"Unknown strategy: {strategy}")
        scores[ticker] = score
    return scores


def choose_strategy(strategy_scores: dict[str, float], rounds: int) -> str:
    exploration_rate = max(0.08, 0.35 * math.exp(-rounds / 45.0))
    if random.random() < exploration_rate:
        return random.choice(list(STRATEGIES))
    best_value = max(strategy_scores.get(name, 0.0) for name in STRATEGIES)
    best = [name for name in STRATEGIES if strategy_scores.get(name, 0.0) == best_value]
    return random.choice(best)


def softmax(values: list[float], temperature: float = 0.85) -> list[float]:
    scaled = [value / max(temperature, 1e-9) for value in values]
    maximum = max(scaled)
    exponentials = [math.exp(value - maximum) for value in scaled]
    total = sum(exponentials)
    return [value / total for value in exponentials]


def choose_allocation(
    features: dict[str, dict[str, float]],
    strategy: str,
    max_positions: int,
    excluded_tickers: Iterable[str] = (),
    news_signals: dict[str, NewsSignal] | None = None,
    news_weight: float = NEWS_SCORE_WEIGHT,
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    excluded = set(clean_tickers(excluded_tickers))
    price_scores = strategy_stock_scores(features, strategy)
    if excluded:
        price_scores = {ticker: score for ticker, score in price_scores.items() if ticker not in excluded}
    if not price_scores and excluded:
        price_scores = strategy_stock_scores(features, strategy)
    if not price_scores:
        raise RuntimeError("The bot could not score any companies.")

    news_signals = news_signals or {}
    combined_scores: dict[str, float] = {}
    details: dict[str, dict[str, float]] = {}
    for ticker, price_score in price_scores.items():
        news_score = float(news_signals.get(ticker).score) if ticker in news_signals else 0.0
        combined = price_score + news_weight * news_score
        combined_scores[ticker] = combined
        details[ticker] = {
            "price_score": price_score,
            "news_score": news_score,
            "combined_score": combined,
        }

    ranked = sorted(combined_scores.items(), key=lambda item: item[1], reverse=True)
    limit = min(max_positions, len(ranked))

    if limit == 1:
        count = 1
    else:
        score_values = [value for _, value in ranked]
        spread = statistics.pstdev(score_values) or 1.0
        gap = (ranked[0][1] - ranked[1][1]) / spread
        if gap >= 1.0:
            count = 1
        elif gap >= 0.45:
            count = min(2, limit)
        else:
            count = min(3, limit)
        if random.random() < 0.12 and count < limit:
            count += 1

    selected = ranked[:count]
    weights = softmax([score for _, score in selected])
    allocation = {ticker: weight for (ticker, _), weight in zip(selected, weights)}
    total = sum(allocation.values())
    normalized = {ticker: weight / total for ticker, weight in allocation.items()}
    return normalized, details


def effective_news_weight(state: BotState) -> float:
    return max(0.25, min(1.10, NEWS_SCORE_WEIGHT + 0.45 * state.news_learning_score))


def selected_news_snapshot(state: BotState) -> dict[str, Any]:
    source = state.entry_news_signals or state.news_signals
    return {
        ticker: source.get(ticker, {})
        for ticker in state.allocation
        if source.get(ticker)
    }


def selected_news_summary(state: BotState, max_headlines: int = 3) -> str:
    source = state.entry_news_signals or state.news_signals
    parts: list[str] = []
    for ticker in state.allocation:
        signal = source.get(ticker, {})
        headline = str(signal.get("top_headline") or "").strip()
        label = str(signal.get("label") or "Neutral")
        if headline and not headline.startswith("No company-specific"):
            parts.append(f"{ticker} {label}: {headline}")
        else:
            parts.append(f"{ticker}: no recent headline")
        if len(parts) >= max_headlines:
            break
    return " | ".join(parts)


def weighted_news_score(state: BotState, use_entry: bool = False) -> float:
    source = state.entry_news_signals if use_entry and state.entry_news_signals else state.news_signals
    total = 0.0
    for ticker, weight in state.allocation.items():
        signal = source.get(ticker, {})
        try:
            total += weight * float(signal.get("score", 0.0) or 0.0)
        except (TypeError, ValueError):
            pass
    return max(-1.0, min(1.0, total))


def make_snapshot(state: BotState, quotes: dict[str, Quote]) -> Snapshot:
    portfolio_return = 0.0
    prices: dict[str, float] = {}
    market_dates: dict[str, str] = {}
    for ticker, weight in state.allocation.items():
        quote = quotes.get(ticker)
        entry = state.entry_prices.get(ticker)
        if quote is None or entry is None or entry <= 0:
            continue
        prices[ticker] = quote.price
        market_dates[ticker] = quote.market_date
        portfolio_return += weight * (quote.price / entry - 1.0)

    round_points = portfolio_return * 100.0
    live_cash = max(0.0, state.cash_at_round_start * (1.0 + portfolio_return))
    return Snapshot(
        prices=prices,
        market_dates=market_dates,
        portfolio_return=portfolio_return,
        round_points=round_points,
        total_points=state.banked_points + round_points,
        live_cash=live_cash,
        fetched_at=datetime.now().astimezone(),
    )


def settle_round(state: BotState, snapshot: Snapshot) -> None:
    if not state.allocation:
        return
    cash_before = state.cash_at_round_start
    completed_companies = list(state.allocation)
    market_date = max(
        snapshot.market_dates.values(),
        default=datetime.now().astimezone().date().isoformat(),
    )
    news_direction = weighted_news_score(state, use_entry=True)
    if abs(news_direction) >= 0.05:
        agreement = max(-1.0, min(1.0, news_direction * snapshot.round_points))
        state.news_learning_score = (
            0.85 * state.news_learning_score + 0.15 * agreement
        )

    state.cash_at_round_start = snapshot.live_cash
    state.banked_points += snapshot.round_points
    state.rounds_completed += 1
    state.last_round_tickers = completed_companies

    if state.active_strategy:
        old_score = state.strategy_scores.get(state.active_strategy, 0.0)
        state.strategy_scores[state.active_strategy] = 0.82 * old_score + 0.18 * snapshot.round_points

    append_history(
        {
            "market_date": market_date,
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "strategy": state.active_strategy or "",
            "cash_before": f"{cash_before:.6f}",
            "cash_after": f"{state.cash_at_round_start:.6f}",
            "point_change": f"{snapshot.round_points:.6f}",
            "banked_points": f"{state.banked_points:.6f}",
            "companies": ", ".join(completed_companies),
            "allocation": json.dumps(state.allocation, sort_keys=True),
            "news_summary": selected_news_summary(state),
            "news_snapshot": json.dumps(selected_news_snapshot(state), sort_keys=True),
        }
    )


def estimated_market_status() -> tuple[str, bool]:
    eastern = eastern_datetime_from_timestamp(time.time())
    is_weekday = eastern.weekday() < 5
    open_now = is_weekday and clock_time(9, 30) <= eastern.time().replace(tzinfo=None) < clock_time(16, 0)
    if open_now:
        return "Market likely open", True
    return "Market closed / prices may be delayed", False


