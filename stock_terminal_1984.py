#!/usr/bin/env python3
"""
1984-style stock terminal for macOS.

Launch prompts for ticker, layout, investment amount, and optional Twelve Data key.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import random
import sys
import threading
import time
import http.cookiejar
import urllib.error
import urllib.parse
import urllib.request
import tkinter as tk
from tkinter import simpledialog
from tkinter import font as tkfont
from zoneinfo import ZoneInfo


BG_COLOR = "#050505"
TEXT_COLOR = "#77FFAA"
DIM_TEXT_COLOR = "#6FB892"
ERROR_COLOR = "#FF8A8A"
CREDITS_NAME = "Jack S"

# Approximate stripe colors from the classic rainbow Apple logo.
APPLE_1984_COLORS = [
    "#5CB85C",  # green
    "#F0E14A",  # yellow
    "#F4A340",  # orange
    "#E85A5A",  # red
    "#B266FF",  # violet
    "#4D9CFF",  # blue
]

# Compact text logo matching the striped Apple silhouette.
APPLE_LOGO_ART = [
    ("   ###", 0),
    ("  #####", 0),
    (" #######", 1),
    ("##########", 2),
    ("######   #", 3),
    ("##########", 4),
    (" #######", 5),
    ("  ##  ##", 5),
]

QUOTE_SUMMARY_MODULES = [
    "assetProfile",
    "summaryProfile",
    "summaryDetail",
    "financialData",
    "defaultKeyStatistics",
    "calendarEvents",
    "price",
    "quoteType",
    "esgScores",
    "incomeStatementHistory",
    "incomeStatementHistoryQuarterly",
    "balanceSheetHistory",
    "balanceSheetHistoryQuarterly",
    "cashflowStatementHistory",
    "cashflowStatementHistoryQuarterly",
    "earnings",
    "earningsHistory",
    "earningsTrend",
    "recommendationTrend",
    "upgradeDowngradeHistory",
    "netSharePurchaseActivity",
    "insiderHolders",
    "insiderTransactions",
    "institutionOwnership",
    "fundOwnership",
    "majorDirectHolders",
    "majorHoldersBreakdown",
    "secFilings",
    "industryTrend",
    "sectorTrend",
    "indexTrend",
    "fundProfile",
    "topHoldings",
    "fundPerformance",
]


def normalize_logo_side(value: str) -> str:
    value = value.strip().lower()
    if value in {"l", "left"}:
        return "left"
    if value in {"r", "right"}:
        return "right"
    return ""


def parse_investment_amount_text(raw: str) -> float | None:
    cleaned = raw.strip().replace("$", "").replace(",", "")
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value < 0:
        return None
    return value


def scalar_to_text(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.8g}"
    return str(value)


def flatten_json(value: object, prefix: str) -> list[str]:
    lines: list[str] = []
    if isinstance(value, dict):
        if not value:
            lines.append(f"{prefix} = {{}}")
            return lines
        for key in sorted(value.keys()):
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            lines.extend(flatten_json(value[key], child_prefix))
        return lines
    if isinstance(value, list):
        if not value:
            lines.append(f"{prefix} = []")
            return lines
        for idx, item in enumerate(value):
            child_prefix = f"{prefix}[{idx}]"
            lines.extend(flatten_json(item, child_prefix))
        return lines
    lines.append(f"{prefix} = {scalar_to_text(value)}")
    return lines


class YahooFinanceClient:
    def __init__(self, timeout_seconds: int = 12, twelvedata_api_key: str = ""):
        self.timeout_seconds = timeout_seconds
        self.twelvedata_api_key = twelvedata_api_key.strip()
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
            "Referer": "https://finance.yahoo.com/",
        }
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar)
        )
        self.crumb: str | None = None
        self.use_yahoo_endpoints = True
        if self.use_yahoo_endpoints:
            self._initialize_session()

    @staticmethod
    def _to_twelvedata_interval(interval: str) -> str:
        normalized = str(interval or "").strip().lower()
        mapping = {
            "1m": "1min",
            "2m": "1min",
            "5m": "5min",
            "15m": "15min",
            "30m": "30min",
            "60m": "1h",
            "90m": "1h",
            "1d": "1day",
            "1wk": "1week",
            "1mo": "1month",
        }
        return mapping.get(normalized, "5min")

    def _fetch_twelvedata_json(
        self,
        endpoint: str,
        params: dict[str, object] | None = None,
        deadline_epoch: float | None = None,
    ) -> dict:
        if not self.twelvedata_api_key:
            raise ValueError("Twelve Data API key not configured")
        final_params = dict(params or {})
        final_params["apikey"] = self.twelvedata_api_key
        query = urllib.parse.urlencode(final_params, doseq=True, safe=",")
        url = f"https://api.twelvedata.com/{endpoint}"
        if query:
            url = f"{url}?{query}"
        if deadline_epoch is not None:
            remaining = deadline_epoch - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("refresh time budget exhausted")
            request_timeout = max(0.75, min(self.timeout_seconds, remaining))
        else:
            request_timeout = self.timeout_seconds
        request_headers = {
            "User-Agent": self.headers["User-Agent"],
            "Accept": "application/json",
            "Connection": "keep-alive",
        }
        request = urllib.request.Request(url=url, headers=request_headers)
        with self.opener.open(request, timeout=request_timeout) as response:
            payload = response.read().decode("utf-8")
        parsed = json.loads(payload)
        if isinstance(parsed, dict) and str(parsed.get("status", "")).lower() == "error":
            message = str(parsed.get("message") or "Twelve Data error")
            raise ValueError(message)
        return parsed

    def _initialize_session(self, force: bool = False) -> None:
        if self.crumb and not force:
            return
        self.crumb = None
        try:
            warm = urllib.request.Request(
                url="https://fc.yahoo.com",
                headers=self.headers,
            )
            self.opener.open(warm, timeout=self.timeout_seconds).read()
        except Exception:
            pass
        try:
            crumb_req = urllib.request.Request(
                url="https://query1.finance.yahoo.com/v1/test/getcrumb",
                headers=self.headers,
            )
            with self.opener.open(crumb_req, timeout=self.timeout_seconds) as response:
                candidate = response.read().decode("utf-8").strip()
                if candidate:
                    self.crumb = candidate
        except Exception:
            self.crumb = None

    def _fetch_json(
        self,
        url: str,
        params: dict[str, object] | None = None,
        deadline_epoch: float | None = None,
    ) -> dict:
        if params:
            final_params = dict(params)
        else:
            final_params = {}
        final_params["corsDomain"] = "finance.yahoo.com"
        if self.crumb:
            final_params["crumb"] = self.crumb
        query = urllib.parse.urlencode(final_params, doseq=True, safe=",")
        if query:
            url = f"{url}?{query}"
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                if deadline_epoch is not None:
                    remaining = deadline_epoch - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("refresh time budget exhausted")
                    request_timeout = max(0.75, min(self.timeout_seconds, remaining))
                else:
                    request_timeout = self.timeout_seconds
                request_headers = dict(self.headers)
                request_headers["User-Agent"] = (
                    self.headers["User-Agent"] + f" v{random.randint(1000, 9999)}"
                )
                request = urllib.request.Request(url=url, headers=request_headers)
                with self.opener.open(request, timeout=request_timeout) as response:
                    payload = response.read().decode("utf-8")
                return json.loads(payload)
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code == 429:
                    if attempt == 0:
                        self._initialize_session(force=True)
                        sleep_time = 0.5 + random.random()
                        if deadline_epoch is not None:
                            sleep_time = min(sleep_time, max(0.0, deadline_epoch - time.monotonic()))
                        if sleep_time > 0:
                            time.sleep(sleep_time)
                        continue
                    raise
                if attempt < 1:
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_exc = exc
                if attempt < 1:
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Unknown fetch failure")

    def _fetch_first_success(
        self,
        candidates: list[tuple[str, dict[str, object]]],
        deadline_epoch: float | None = None,
    ) -> dict:
        last_exc: Exception | None = None
        for url, params in candidates:
            try:
                return self._fetch_json(url, params, deadline_epoch=deadline_epoch)
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_exc = exc
            except urllib.error.HTTPError as exc:
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("No endpoint candidates provided")

    def fetch_yahoo_quote(self, symbol: str, deadline_epoch: float | None = None) -> dict:
        params = {
            "symbols": symbol,
            "lang": "en-US",
            "region": "US",
        }
        candidates = [
            ("https://query1.finance.yahoo.com/v7/finance/quote", params),
            ("https://query2.finance.yahoo.com/v7/finance/quote", params),
        ]
        return self._fetch_first_success(candidates, deadline_epoch=deadline_epoch)

    def fetch_twelvedata_quote(self, symbol: str, deadline_epoch: float | None = None) -> dict[str, object]:
        payload = self._fetch_twelvedata_json(
            "quote",
            {
                "symbol": symbol,
                "timezone": "America/New_York",
            },
            deadline_epoch=deadline_epoch,
        )
        if not isinstance(payload, dict):
            raise ValueError("Unexpected Twelve Data quote payload")
        payload["source"] = "twelvedata"
        return payload

    def fetch_twelvedata_chart(
        self,
        symbol: str,
        interval: str,
        outputsize: int = 500,
        deadline_epoch: float | None = None,
    ) -> dict[str, object]:
        td_interval = self._to_twelvedata_interval(interval)
        payload = self._fetch_twelvedata_json(
            "time_series",
            {
                "symbol": symbol,
                "interval": td_interval,
                "outputsize": max(30, min(5000, int(outputsize))),
                "order": "asc",
                "timezone": "America/New_York",
            },
            deadline_epoch=deadline_epoch,
        )
        values = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(values, list) or not values:
            raise ValueError("Twelve Data chart returned no values")
        return {
            "source": "twelvedata",
            "interval": td_interval,
            "meta": payload.get("meta") if isinstance(payload, dict) else {},
            "values": values,
        }

    def fetch_yahoo_chart(
        self,
        symbol: str,
        interval: str,
        range_value: str,
        deadline_epoch: float | None = None,
    ) -> dict:
        params = {
            "interval": interval,
            "range": range_value,
            "includePrePost": "false",
            "events": "div,splits",
            "lang": "en-US",
            "region": "US",
        }
        candidates = [
            (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}", params),
            (f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}", params),
        ]
        return self._fetch_first_success(candidates, deadline_epoch=deadline_epoch)

    @staticmethod
    def _to_stooq_symbol(symbol: str) -> str:
        symbol = symbol.strip().lower()
        if "." in symbol:
            return symbol
        return f"{symbol}.us"

    @staticmethod
    def _normalize_csv_key(key: object) -> str:
        return "".join(ch for ch in str(key).strip().lower() if ch.isalnum())

    @classmethod
    def _normalize_csv_row(cls, row: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, value in row.items():
            norm_key = cls._normalize_csv_key(key)
            if not norm_key:
                continue
            normalized[norm_key] = value
        return normalized

    @classmethod
    def _csv_get(cls, row: dict[str, str], aliases: tuple[str, ...]) -> str:
        for alias in aliases:
            value = row.get(cls._normalize_csv_key(alias))
            if value not in {None, ""}:
                return str(value)
        return ""

    def _fetch_stooq_csv(self, endpoint: str, params: dict[str, str]) -> list[dict[str, str]]:
        query = urllib.parse.urlencode(params, doseq=True)
        url = f"{endpoint}?{query}"
        request = urllib.request.Request(url=url, headers=self.headers)
        with self.opener.open(request, timeout=max(3, self.timeout_seconds)) as response:
            payload = response.read().decode("utf-8", errors="replace")
        delimiter = ","
        try:
            dialect = csv.Sniffer().sniff(payload[:4096], delimiters=",;\t|")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ","
        rows = list(csv.DictReader(io.StringIO(payload), delimiter=delimiter))
        return rows

    def fetch_stooq_quote(self, symbol: str) -> dict[str, object]:
        stooq_symbol = self._to_stooq_symbol(symbol)
        rows = self._fetch_stooq_csv(
            "https://stooq.com/q/l/",
            {"s": stooq_symbol, "f": "sd2t2ohlcv", "h": "", "e": "csv"},
        )
        if not rows:
            raise ValueError("Stooq quote returned no rows")
        row = self._normalize_csv_row(rows[0])

        def to_num(value: str) -> float | None:
            if value in {"", "N/D", None}:  # type: ignore[arg-type]
                return None
            try:
                text = str(value).strip().replace(" ", "")
                if not text or text.upper() in {"N/D", "ND", "-"}:
                    return None
                if text.count(",") == 1 and text.count(".") == 0:
                    text = text.replace(",", ".")
                else:
                    text = text.replace(",", "")
                return float(text)
            except (ValueError, TypeError):
                return None

        return {
            "source": "stooq",
            "symbol": self._csv_get(row, ("symbol", "ticker", "s")),
            "date": self._csv_get(row, ("date", "d")),
            "time": self._csv_get(row, ("time", "t", "time2", "t2")),
            "open": to_num(self._csv_get(row, ("open", "o"))),
            "high": to_num(self._csv_get(row, ("high", "h"))),
            "low": to_num(self._csv_get(row, ("low", "l"))),
            "close": to_num(self._csv_get(row, ("close", "c", "last", "price"))),
            "volume": to_num(self._csv_get(row, ("volume", "vol", "v"))),
        }

    def fetch_stooq_chart(self, symbol: str, interval: str = "d") -> dict[str, object]:
        stooq_symbol = self._to_stooq_symbol(symbol)
        chosen_interval = str(interval or "d").strip().lower()
        endpoint = "https://stooq.com/q/d/l/"

        def to_num(value: object) -> float | None:
            if value in {"", "N/D", None}:  # type: ignore[arg-type]
                return None
            try:
                text = str(value).strip().replace(" ", "")
                if not text or text.upper() in {"N/D", "ND", "-"}:
                    return None
                if text.count(",") == 1 and text.count(".") == 0:
                    text = text.replace(",", ".")
                else:
                    text = text.replace(",", "")
                return float(text)
            except (TypeError, ValueError):
                return None

        def parse_rows(raw_rows: list[dict[str, str]]) -> list[dict[str, object]]:
            cleaned_rows: list[dict[str, object]] = []
            for raw_row in raw_rows[-12000:]:
                row = self._normalize_csv_row(raw_row)
                open_ = to_num(self._csv_get(row, ("open", "o")))
                high = to_num(self._csv_get(row, ("high", "h")))
                low = to_num(self._csv_get(row, ("low", "l")))
                close = to_num(self._csv_get(row, ("close", "c", "last", "price")))
                volume = to_num(self._csv_get(row, ("volume", "vol", "v")))
                if close is None:
                    continue
                cleaned_rows.append(
                    {
                        "date": self._csv_get(row, ("date", "d")),
                        "time": self._csv_get(row, ("time", "t", "time2", "t2")),
                        "open": open_,
                        "high": high,
                        "low": low,
                        "close": close,
                        "volume": volume,
                    }
                )
            return cleaned_rows

        rows = self._fetch_stooq_csv(endpoint, {"s": stooq_symbol, "i": chosen_interval})
        cleaned = parse_rows(rows)
        if not cleaned and chosen_interval != "d":
            chosen_interval = "d"
            rows = self._fetch_stooq_csv(endpoint, {"s": stooq_symbol, "i": chosen_interval})
            cleaned = parse_rows(rows)
        if not cleaned:
            raise ValueError("Stooq chart had no parseable rows")
        return {"source": "stooq", "interval": chosen_interval, "bars": cleaned}

    def fetch_all(
        self,
        symbol: str,
        include_summary: bool = True,
        include_options: bool = True,
        max_total_seconds: int = 12,
        chart_interval: str = "5m",
        chart_range: str = "5d",
        twelvedata_interval: str | None = None,
        stooq_interval: str | None = None,
    ) -> dict[str, object]:
        results: dict[str, object] = {}
        errors: dict[str, str] = {}

        deadline_epoch = time.monotonic() + max(1, int(max_total_seconds))

        quote_payload: dict[str, object] = {}
        stooq_quote_error: str | None = None
        td_exchange = ""
        try:
            quote_payload["fallbackQuote"] = self.fetch_stooq_quote(symbol)
        except Exception as exc:
            stooq_quote_error = str(exc)

        if self.twelvedata_api_key:
            try:
                td_quote = self.fetch_twelvedata_quote(
                    symbol,
                    deadline_epoch=deadline_epoch,
                )
                quote_payload["twelveDataQuote"] = td_quote
                td_exchange = str(td_quote.get("exchange") or "").strip().upper()
            except Exception:
                pass

        if include_summary:
            try:
                yahoo_quote = self.fetch_yahoo_quote(symbol, deadline_epoch=deadline_epoch)
                quote_response = yahoo_quote.get("quoteResponse")
                if isinstance(quote_response, dict):
                    quote_payload["quoteResponse"] = quote_response
            except Exception:
                pass

        if not quote_payload:
            errors["quote"] = stooq_quote_error or "quote unavailable"

        chart_payload: dict[str, object] = {}
        chart_errors: list[str] = []

        if self.twelvedata_api_key:
            td_interval = str(twelvedata_interval or chart_interval or "5m")
            td_symbols: list[str] = []
            if td_exchange:
                td_symbols.append(f"{symbol}:{td_exchange}")
            td_symbols.append(symbol)
            seen_symbols: set[str] = set()
            for td_symbol in td_symbols:
                if not td_symbol or td_symbol in seen_symbols:
                    continue
                seen_symbols.add(td_symbol)
                try:
                    chart_payload["twelveDataChart"] = self.fetch_twelvedata_chart(
                        symbol=td_symbol,
                        interval=td_interval,
                        outputsize=500,
                        deadline_epoch=deadline_epoch,
                    )
                    break
                except Exception as exc:
                    chart_errors.append(f"twelvedata({td_symbol}): {exc}")

        if "twelveDataChart" not in chart_payload:
            try:
                yahoo_chart = self.fetch_yahoo_chart(
                    symbol=symbol,
                    interval=str(chart_interval or "5m"),
                    range_value=str(chart_range or "5d"),
                    deadline_epoch=deadline_epoch,
                )
                chart_obj = yahoo_chart.get("chart")
                if isinstance(chart_obj, dict):
                    chart_payload["chart"] = chart_obj
            except Exception as exc:
                chart_errors.append(f"yahoo: {exc}")

        if "twelveDataChart" not in chart_payload and "chart" not in chart_payload:
            try:
                fallback_interval = str(stooq_interval or chart_interval or "d")
                chart_payload["fallbackChart"] = self.fetch_stooq_chart(symbol, interval=fallback_interval)
            except Exception as exc:
                chart_errors.append(f"stooq: {exc}")

        has_any_chart = "twelveDataChart" in chart_payload or "chart" in chart_payload or "fallbackChart" in chart_payload
        has_any_quote = bool(quote_payload)
        if "fallbackChart" in chart_payload and ("twelveDataChart" not in chart_payload and "chart" not in chart_payload):
            if chart_errors:
                errors["chart"] = " | ".join(chart_errors)
        elif not has_any_chart and not has_any_quote:
            errors["chart"] = " | ".join(chart_errors) if chart_errors else "chart unavailable"

        if quote_payload:
            results["quote"] = quote_payload
        if chart_payload:
            results["chart"] = chart_payload
        results["_errors"] = errors
        results["_notes"] = {"source": "TwelveData + Yahoo + Stooq fallback"}
        return results


class StockTerminalApp:
    GRAPH_MODES = [
        {
            "id": "5min",
            "name": "5 Minute",
            "yahoo_interval": "5m",
            "yahoo_range": "5d",
            "twelvedata_interval": "1m",
            "stooq_interval": "1",
            "downsample": 1,
            "year_aggregate": False,
            "window_seconds": 5 * 60,
            "auto_refresh_seconds": 10,
        },
        {
            "id": "10min",
            "name": "10 Minute",
            "yahoo_interval": "5m",
            "yahoo_range": "10d",
            "twelvedata_interval": "1m",
            "stooq_interval": "1",
            "downsample": 2,
            "year_aggregate": False,
            "window_seconds": 10 * 60,
            "auto_refresh_seconds": 20,
        },
        {
            "id": "15min",
            "name": "15 Minute",
            "yahoo_interval": "15m",
            "yahoo_range": "1mo",
            "twelvedata_interval": "1m",
            "stooq_interval": "1",
            "downsample": 1,
            "year_aggregate": False,
            "window_seconds": 15 * 60,
            "auto_refresh_seconds": 30,
        },
        {
            "id": "30min",
            "name": "30 Minute",
            "yahoo_interval": "30m",
            "yahoo_range": "3mo",
            "twelvedata_interval": "1m",
            "stooq_interval": "1",
            "downsample": 1,
            "year_aggregate": False,
            "window_seconds": 30 * 60,
            "auto_refresh_seconds": 60,
        },
        {
            "id": "hour",
            "name": "Hour",
            "yahoo_interval": "60m",
            "yahoo_range": "6mo",
            "twelvedata_interval": "5m",
            "stooq_interval": "5",
            "downsample": 1,
            "year_aggregate": False,
            "window_seconds": 60 * 60,
            "auto_refresh_seconds": 120,
        },
        {
            "id": "6hour",
            "name": "6 Hour",
            "yahoo_interval": "60m",
            "yahoo_range": "6mo",
            "twelvedata_interval": "15m",
            "stooq_interval": "15",
            "downsample": 6,
            "year_aggregate": False,
            "window_seconds": 6 * 60 * 60,
            "auto_refresh_seconds": 600,
        },
        {
            "id": "day",
            "name": "Day",
            "yahoo_interval": "1d",
            "yahoo_range": "5y",
            "twelvedata_interval": "60m",
            "stooq_interval": "60",
            "downsample": 1,
            "year_aggregate": False,
            "window_seconds": 24 * 60 * 60,
            "auto_refresh_seconds": 900,
        },
        {
            "id": "week",
            "name": "Week",
            "yahoo_interval": "1wk",
            "yahoo_range": "10y",
            "twelvedata_interval": "1d",
            "stooq_interval": "d",
            "downsample": 1,
            "year_aggregate": False,
            "window_seconds": 7 * 24 * 60 * 60,
            "auto_refresh_seconds": 3600,
        },
        {
            "id": "month",
            "name": "Month",
            "yahoo_interval": "1mo",
            "yahoo_range": "max",
            "twelvedata_interval": "1d",
            "stooq_interval": "d",
            "downsample": 1,
            "year_aggregate": False,
            "window_seconds": 30 * 24 * 60 * 60,
            "auto_refresh_seconds": 6 * 3600,
        },
        {
            "id": "year",
            "name": "Year",
            "yahoo_interval": "1mo",
            "yahoo_range": "max",
            "twelvedata_interval": "1wk",
            "stooq_interval": "w",
            "downsample": 1,
            "year_aggregate": True,
            "window_seconds": 365 * 24 * 60 * 60,
            "auto_refresh_seconds": 24 * 3600,
        },
    ]

    METRIC_FIELDS = [
        "Name",
        "Price",
        "Change",
        "Position Value",
        "Today P/L",
        "Est Shares",
        "Open",
        "Day Range",
        "Prev Close",
        "Volume",
        "Market Cap",
        "Exchange",
        "Currency",
        "Quote Source",
        "Chart Source",
    ]

    def __init__(
        self,
        root: tk.Tk,
        symbol: str,
        logo_side: str,
        investment_amount: float,
        refresh_seconds: int,
        timeout_seconds: int,
        full_refresh_seconds: int,
        twelvedata_api_key: str = "",
    ):
        self.root = root
        self.symbol = symbol.upper()
        self.logo_side = logo_side
        self.investment_amount = max(0.0, float(investment_amount))
        self.estimated_shares: float | None = None
        self.refresh_seconds = max(5, refresh_seconds)
        self.dynamic_refresh_seconds = self.refresh_seconds
        self.refresh_manual_override = False
        self.full_refresh_seconds = max(60, full_refresh_seconds)
        self.next_full_refresh_epoch = time.time() + self.full_refresh_seconds
        self.client = YahooFinanceClient(timeout_seconds=timeout_seconds, twelvedata_api_key=twelvedata_api_key)
        self.est_tz = ZoneInfo("America/New_York")

        self.refresh_count = 0
        self.in_flight = False
        self.force_full_next = False
        self.resize_job: str | None = None
        self.refresh_job: str | None = None
        self.cached_sections: dict[str, object] = {}
        self.cached_section_times: dict[str, float] = {}
        self.graph_values: list[float] = []
        self.graph_labels: list[str] = []
        self.graph_source = "n/a"
        self.graph_bars: list[dict[str, object]] = []
        self.graph_plot_bounds = (0.0, 0.0, 0.0, 0.0)
        self.bottom_base_text = "Booting terminal..."
        self.refresh_id = 0
        self.graph_mode_index = 0
        self.graph_mode_map = {mode["id"]: mode for mode in self.GRAPH_MODES}
        self.selection_start_idx: int | None = None
        self.selection_end_idx: int | None = None
        self.selection_dragging = False
        self.selection_summary = ""
        self.price_history: list[tuple[dt.datetime, float]] = []

        self.text_font = tkfont.Font(family="Menlo", size=13)
        self.ui_font = tkfont.Font(family="Menlo", size=13, weight="bold")
        self.logo_font = tkfont.Font(family="Menlo", size=9, weight="bold")
        self.metric_key_font = tkfont.Font(family="Menlo", size=12)
        self.metric_value_font = tkfont.Font(family="Menlo", size=12, weight="bold")

        self.metric_vars: dict[str, tk.StringVar] = {
            field: tk.StringVar(value="--") for field in self.METRIC_FIELDS
        }
        self.metric_key_labels: list[tk.Label] = []
        self.metric_value_labels: list[tk.Label] = []
        self.logo_base_color_indices: list[int] = []

        self._build_ui()
        self._recompute_fonts(self.root.winfo_width(), self.root.winfo_height())
        self._apply_mode_refresh_defaults()
        self._schedule_refresh(100)

    def _build_ui(self) -> None:
        self.root.title(f"1984 Stock Terminal - {self.symbol}")
        self.root.geometry("1260x820")
        self.root.minsize(840, 500)
        self.root.configure(bg=BG_COLOR)

        header = tk.Frame(self.root, bg=BG_COLOR, highlightbackground="#1A1A1A", highlightthickness=1)
        header.pack(fill="x", side="top")

        self.info_frame = tk.Frame(header, bg=BG_COLOR)
        self.logo_frame = tk.Frame(header, bg=BG_COLOR)

        if self.logo_side == "left":
            self.logo_frame.pack(side="left", padx=12, pady=8)
            self.info_frame.pack(side="left", fill="x", expand=True, padx=12, pady=8)
            logo_anchor = "w"
            logo_justify = "left"
        else:
            self.info_frame.pack(side="left", fill="x", expand=True, padx=12, pady=8)
            self.logo_frame.pack(side="right", padx=12, pady=8)
            logo_anchor = "e"
            logo_justify = "right"

        self.logo_labels: list[tk.Label] = []
        self.logo_base_color_indices = []
        for line, color_index in APPLE_LOGO_ART:
            label = tk.Label(
                self.logo_frame,
                text=line,
                font=self.logo_font,
                bg=BG_COLOR,
                fg=APPLE_1984_COLORS[color_index],
                anchor=logo_anchor,
                justify=logo_justify,
            )
            label.pack(anchor=logo_anchor)
            self.logo_labels.append(label)
            self.logo_base_color_indices.append(color_index)

        self.title_label = tk.Label(
            self.info_frame,
            text=f"1984 STOCK TERMINAL  [{self.symbol}]",
            font=self.ui_font,
            bg=BG_COLOR,
            fg=TEXT_COLOR,
            anchor="w",
            justify="left",
        )
        self.title_label.pack(anchor="w")

        self.status_label = tk.Label(
            self.info_frame,
            text=f"Waiting for first refresh | interval={self.dynamic_refresh_seconds}s",
            font=self.text_font,
            bg=BG_COLOR,
            fg=DIM_TEXT_COLOR,
            anchor="w",
            justify="left",
        )
        self.status_label.pack(anchor="w", pady=(4, 0))

        self.hint_label = tk.Label(
            self.info_frame,
            text="Hotkeys: R = refresh, F = full refresh, S = stock, G = graph mode, T = refresh sec, Q = quit",
            font=self.text_font,
            bg=BG_COLOR,
            fg=DIM_TEXT_COLOR,
            anchor="w",
            justify="left",
        )
        self.hint_label.pack(anchor="w", pady=(2, 0))

        body = tk.Frame(self.root, bg=BG_COLOR, highlightbackground="#1A1A1A", highlightthickness=1)
        body.pack(fill="both", expand=True)

        side_panel = tk.Frame(body, bg=BG_COLOR, width=360, highlightbackground="#1A1A1A", highlightthickness=1)
        side_panel.pack(side="left", fill="y")
        side_panel.pack_propagate(False)

        side_title = tk.Label(
            side_panel,
            text="LIVE METRICS",
            font=self.ui_font,
            bg=BG_COLOR,
            fg=TEXT_COLOR,
            anchor="w",
            justify="left",
        )
        side_title.pack(fill="x", padx=12, pady=(10, 6))

        for field in self.METRIC_FIELDS:
            row = tk.Frame(side_panel, bg=BG_COLOR)
            row.pack(fill="x", padx=12, pady=2)
            key_label = tk.Label(
                row,
                text=f"{field}:",
                font=self.metric_key_font,
                bg=BG_COLOR,
                fg=DIM_TEXT_COLOR,
                anchor="w",
                justify="left",
            )
            key_label.pack(side="left")
            value_label = tk.Label(
                row,
                textvariable=self.metric_vars[field],
                font=self.metric_value_font,
                bg=BG_COLOR,
                fg=TEXT_COLOR,
                anchor="w",
                justify="left",
            )
            value_label.pack(side="left", padx=(8, 0), fill="x", expand=True)
            self.metric_key_labels.append(key_label)
            self.metric_value_labels.append(value_label)

        warnings_row = tk.Frame(side_panel, bg=BG_COLOR)
        warnings_row.pack(fill="x", padx=12, pady=(8, 0))
        warnings_key = tk.Label(
            warnings_row,
            text="Warnings:",
            font=self.metric_key_font,
            bg=BG_COLOR,
            fg=DIM_TEXT_COLOR,
            anchor="w",
            justify="left",
        )
        warnings_key.pack(side="left")
        self.metric_key_labels.append(warnings_key)

        self.warnings_value = tk.Label(
            side_panel,
            text="none",
            font=self.text_font,
            bg=BG_COLOR,
            fg=DIM_TEXT_COLOR,
            anchor="nw",
            justify="left",
            wraplength=330,
        )
        self.warnings_value.pack(fill="x", padx=12, pady=(2, 10))

        credits_frame = tk.Frame(side_panel, bg=BG_COLOR, highlightbackground="#1A1A1A", highlightthickness=1)
        credits_frame.pack(fill="x", padx=12, pady=(0, 10))
        self.credits_title_label = tk.Label(
            credits_frame,
            text=f"Credits: {CREDITS_NAME}",
            font=self.metric_key_font,
            bg=BG_COLOR,
            fg=DIM_TEXT_COLOR,
            anchor="w",
            justify="left",
        )
        self.credits_title_label.pack(fill="x", padx=8, pady=6)

        graph_panel = tk.Frame(body, bg=BG_COLOR)
        graph_panel.pack(side="left", fill="both", expand=True)

        self.graph_title_label = tk.Label(
            graph_panel,
            text=f"PRICE BAR GRAPH ({self._current_graph_mode()['name']})",
            font=self.ui_font,
            bg=BG_COLOR,
            fg=TEXT_COLOR,
            anchor="w",
            justify="left",
        )
        self.graph_title_label.pack(fill="x", padx=12, pady=(10, 6))

        self.graph_canvas = tk.Canvas(
            graph_panel,
            bg=BG_COLOR,
            highlightthickness=0,
            bd=0,
        )
        self.graph_canvas.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.graph_canvas.bind("<Configure>", lambda _event: self._redraw_graph())
        self.graph_canvas.bind("<Motion>", self._on_graph_motion)
        self.graph_canvas.bind("<Leave>", self._on_graph_leave)
        self.graph_canvas.bind("<ButtonPress-1>", self._on_graph_press)
        self.graph_canvas.bind("<B1-Motion>", self._on_graph_drag)
        self.graph_canvas.bind("<ButtonRelease-1>", self._on_graph_release)

        bottom_bar = tk.Frame(self.root, bg=BG_COLOR, highlightbackground="#1A1A1A", highlightthickness=1)
        bottom_bar.pack(fill="x", side="bottom")
        self.bottom_label = tk.Label(
            bottom_bar,
            text="Booting terminal...",
            font=self.text_font,
            bg=BG_COLOR,
            fg=DIM_TEXT_COLOR,
            anchor="w",
            justify="left",
            padx=12,
            pady=8,
        )
        self.bottom_label.pack(fill="x")

        self.root.bind("<Configure>", self._on_configure)
        self.root.bind("<KeyPress-r>", lambda _event: self.trigger_refresh())
        self.root.bind("<KeyPress-R>", lambda _event: self.trigger_refresh())
        self.root.bind("<KeyPress-f>", lambda _event: self.trigger_full_refresh())
        self.root.bind("<KeyPress-F>", lambda _event: self.trigger_full_refresh())
        self.root.bind("<KeyPress-s>", lambda _event: self.prompt_symbol_change())
        self.root.bind("<KeyPress-S>", lambda _event: self.prompt_symbol_change())
        self.root.bind("<KeyPress-g>", lambda _event: self.prompt_graph_mode_change())
        self.root.bind("<KeyPress-G>", lambda _event: self.prompt_graph_mode_change())
        self.root.bind("<KeyPress-t>", lambda _event: self.prompt_refresh_interval_change())
        self.root.bind("<KeyPress-T>", lambda _event: self.prompt_refresh_interval_change())
        self.root.bind("<KeyPress-q>", lambda _event: self.root.destroy())
        self.root.bind("<KeyPress-Q>", lambda _event: self.root.destroy())

    def _on_configure(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        if self.resize_job is not None:
            self.root.after_cancel(self.resize_job)
        width = event.width
        height = event.height
        self.resize_job = self.root.after(60, lambda: self._recompute_fonts(width, height))

    def _recompute_fonts(self, width: int, height: int) -> None:
        base = int(min(width / 93, height / 41))
        base = max(9, min(29, base))
        self.text_font.configure(size=base)
        self.ui_font.configure(size=max(10, base))
        self.logo_font.configure(size=max(7, base - 4))
        self.metric_key_font.configure(size=max(9, base - 1))
        self.metric_value_font.configure(size=max(10, base))
        if hasattr(self, "warnings_value"):
            self.warnings_value.configure(wraplength=max(220, width // 4))
        if hasattr(self, "credits_title_label"):
            self.credits_title_label.configure(font=self.metric_key_font)
        self._redraw_graph()

    def _schedule_refresh(self, delay_ms: int | None = None) -> None:
        if self.refresh_job is not None:
            self.root.after_cancel(self.refresh_job)
        delay = self.dynamic_refresh_seconds * 1000 if delay_ms is None else max(0, delay_ms)
        self.refresh_job = self.root.after(delay, self.trigger_refresh)

    def trigger_refresh(self) -> None:
        if self.in_flight:
            self._schedule_refresh(1000)
            return
        self.in_flight = True
        self.refresh_id += 1
        current_refresh_id = self.refresh_id
        symbol_snapshot = self.symbol
        self.status_label.configure(text=f"Refreshing {symbol_snapshot}...", fg=DIM_TEXT_COLOR)
        worker = threading.Thread(
            target=self._refresh_worker,
            args=(current_refresh_id, symbol_snapshot),
            daemon=True,
        )
        worker.start()

    def trigger_full_refresh(self) -> None:
        self.force_full_next = True
        self.trigger_refresh()

    def _current_graph_mode(self) -> dict[str, object]:
        if not self.GRAPH_MODES:
            return {
                "id": "5min",
                "name": "5 Minute",
                "yahoo_interval": "5m",
                "yahoo_range": "5d",
                "twelvedata_interval": "1m",
                "stooq_interval": "1",
                "downsample": 1,
                "year_aggregate": False,
                "window_seconds": 5 * 60,
                "auto_refresh_seconds": 10,
            }
        idx = max(0, min(self.graph_mode_index, len(self.GRAPH_MODES) - 1))
        return self.GRAPH_MODES[idx]

    def _apply_mode_refresh_defaults(self) -> None:
        if self.refresh_manual_override:
            return
        mode = self._current_graph_mode()
        auto_seconds = int(mode.get("auto_refresh_seconds", self.refresh_seconds))
        auto_seconds = max(1, auto_seconds)
        self.refresh_seconds = auto_seconds
        self.dynamic_refresh_seconds = auto_seconds

    def prompt_graph_mode_change(self) -> None:
        current = self._current_graph_mode()
        option_lines = [f"{i + 1}. {mode['name']}" for i, mode in enumerate(self.GRAPH_MODES)]
        response = simpledialog.askstring(
            "Graph Mode",
            "Choose graph mode by number or name:\n"
            + "\n".join(option_lines)
            + f"\n\nCurrent: {current['name']}",
            parent=self.root,
        )
        if response is None:
            return
        raw = response.strip().lower()
        if not raw:
            return
        selected: dict[str, object] | None = None
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(self.GRAPH_MODES):
                selected = self.GRAPH_MODES[idx]
        else:
            normalized = raw.replace(" ", "")
            for mode in self.GRAPH_MODES:
                mode_name = str(mode["name"]).lower().replace(" ", "")
                mode_id = str(mode["id"]).lower().replace(" ", "")
                if normalized in {mode_name, mode_id}:
                    selected = mode
                    break
        if selected is None:
            self.bottom_label.configure(
                text=f"{self.bottom_base_text} | Invalid mode entry",
                fg=ERROR_COLOR,
            )
            return
        self._set_graph_mode(str(selected["id"]))

    def _set_graph_mode(self, mode_id: str) -> None:
        for idx, mode in enumerate(self.GRAPH_MODES):
            if str(mode["id"]) == mode_id:
                self.graph_mode_index = idx
                break
        mode = self._current_graph_mode()
        self.refresh_manual_override = False
        self._apply_mode_refresh_defaults()
        self.graph_title_label.configure(text=f"PRICE BAR GRAPH ({mode['name']})")
        self.selection_start_idx = None
        self.selection_end_idx = None
        self.selection_dragging = False
        self.selection_summary = ""
        self.graph_values = []
        self.graph_labels = []
        self.cached_sections.pop("chart", None)
        self.cached_section_times.pop("chart", None)
        self.force_full_next = False
        self.refresh_id += 1  # invalidate stale callbacks from old mode
        self.in_flight = False
        self.bottom_base_text = (
            f"Graph mode {mode['name']} | refresh {self.refresh_seconds}s | refreshing..."
        )
        self.bottom_label.configure(text=self.bottom_base_text, fg=DIM_TEXT_COLOR)
        self.trigger_refresh()

    def prompt_refresh_interval_change(self) -> None:
        typed = simpledialog.askstring(
            "Refresh Frequency",
            "Refresh every how many seconds?",
            initialvalue=str(self.refresh_seconds),
            parent=self.root,
        )
        if typed is None:
            return
        try:
            seconds = int(round(float(typed.strip())))
        except ValueError:
            self.bottom_label.configure(
                text=f"{self.bottom_base_text} | Invalid refresh seconds",
                fg=ERROR_COLOR,
            )
            return
        if seconds < 1:
            self.bottom_label.configure(
                text=f"{self.bottom_base_text} | Refresh seconds must be >= 1",
                fg=ERROR_COLOR,
            )
            return
        self.refresh_manual_override = True
        self.refresh_seconds = seconds
        self.dynamic_refresh_seconds = seconds
        self.bottom_base_text = f"Refresh interval set to {seconds}s"
        self.bottom_label.configure(text=self.bottom_base_text, fg=DIM_TEXT_COLOR)
        self._schedule_refresh()

    def prompt_symbol_change(self) -> None:
        typed = simpledialog.askstring(
            "Change Ticker",
            "Enter ticker symbol:",
            initialvalue=self.symbol,
            parent=self.root,
        )
        if typed is None:
            return
        normalized = typed.strip().upper()
        if not normalized:
            return
        amount = self._prompt_investment_amount_dialog(self.investment_amount)
        if amount is None:
            return
        self._switch_symbol(normalized, amount)

    def _prompt_investment_amount_dialog(self, initial_amount: float) -> float | None:
        while True:
            typed = simpledialog.askstring(
                "Position Size",
                "Total amount you currently have in this stock/index fund (USD):",
                initialvalue=f"{initial_amount:.2f}",
                parent=self.root,
            )
            if typed is None:
                return None
            parsed = parse_investment_amount_text(typed)
            if parsed is not None:
                return parsed
            self.bottom_label.configure(
                text=f"{self.bottom_base_text} | Invalid amount (use numbers like 12500.50)",
                fg=ERROR_COLOR,
            )

    def _switch_symbol(self, symbol: str, investment_amount: float) -> None:
        if symbol == self.symbol:
            self.investment_amount = max(0.0, float(investment_amount))
            self.estimated_shares = None
            self.bottom_base_text = f"Updated invested amount to {self._fmt_currency(self.investment_amount)}"
            self.bottom_label.configure(text=self.bottom_base_text, fg=DIM_TEXT_COLOR)
            self.trigger_refresh()
            return
        self.symbol = symbol
        self.investment_amount = max(0.0, float(investment_amount))
        self.estimated_shares = None
        self.root.title(f"1984 Stock Terminal - {self.symbol}")
        self.title_label.configure(text=f"1984 STOCK TERMINAL  [{self.symbol}]")

        self.cached_sections.clear()
        self.cached_section_times.clear()
        self.graph_values = []
        self.graph_labels = []
        self.graph_bars = []
        self.graph_source = "n/a"
        self.price_history = []
        self.selection_start_idx = None
        self.selection_end_idx = None
        self.selection_dragging = False
        self.selection_summary = ""
        self.dynamic_refresh_seconds = self.refresh_seconds
        if not self.refresh_manual_override:
            self._apply_mode_refresh_defaults()
        self.next_full_refresh_epoch = time.time() + self.full_refresh_seconds
        self.force_full_next = False
        self.in_flight = False
        self.refresh_id += 1  # invalidate stale worker callbacks
        for field in self.METRIC_FIELDS:
            self.metric_vars[field].set("--")
        self.warnings_value.configure(text="none", fg=DIM_TEXT_COLOR)
        self.bottom_base_text = f"Switched to {self.symbol}. Refreshing..."
        self.bottom_label.configure(text=self.bottom_base_text, fg=DIM_TEXT_COLOR)
        self.status_label.configure(text=f"Switched to {self.symbol}. Refreshing...", fg=DIM_TEXT_COLOR)
        self._redraw_graph()
        self.trigger_refresh()

    def _refresh_worker(self, refresh_id: int, symbol_snapshot: str) -> None:
        started = time.time()
        try:
            now = time.time()
            include_heavy = self.force_full_next or now >= self.next_full_refresh_epoch
            if self.refresh_count == 0:
                budget_seconds = 12
            else:
                budget_seconds = 20 if include_heavy else 12

            graph_mode = self._current_graph_mode()
            payload = self.client.fetch_all(
                symbol_snapshot,
                include_summary=include_heavy,
                include_options=include_heavy,
                max_total_seconds=budget_seconds,
                chart_interval=str(graph_mode["yahoo_interval"]),
                chart_range=str(graph_mode["yahoo_range"]),
                twelvedata_interval=str(graph_mode.get("twelvedata_interval", graph_mode["yahoo_interval"])),
                stooq_interval=str(graph_mode.get("stooq_interval", graph_mode["yahoo_interval"])),
            )
            elapsed = time.time() - started
            self.root.after(
                0,
                lambda: self._apply_refresh(
                    payload,
                    elapsed,
                    include_heavy,
                    now,
                    refresh_id,
                    symbol_snapshot,
                ),
            )
        except Exception as exc:
            elapsed = time.time() - started
            self.root.after(
                0,
                lambda: self._apply_refresh_failure(
                    exc,
                    elapsed,
                    refresh_id,
                    symbol_snapshot,
                ),
            )

    def _apply_refresh_failure(
        self,
        exc: Exception,
        elapsed: float,
        refresh_id: int,
        symbol_snapshot: str,
    ) -> None:
        if refresh_id != self.refresh_id or symbol_snapshot != self.symbol:
            return
        self.status_label.configure(
            text=f"Refresh failed in {elapsed:.2f}s | retrying in {self.dynamic_refresh_seconds}s",
            fg=ERROR_COLOR,
        )
        self.warnings_value.configure(
            text=f"{type(exc).__name__}: {exc}",
            fg=ERROR_COLOR,
        )
        self.bottom_label.configure(
            text="Refresh failed. R = retry now | F = full refresh | S = stock | G = graph mode | Q = quit",
            fg=ERROR_COLOR,
        )
        self.in_flight = False
        self._schedule_refresh()

    def _update_cache(self, payload: dict[str, object], timestamp: float) -> None:
        for section in ("quote", "chart", "summary", "options"):
            if section in payload:
                self.cached_sections[section] = payload[section]
                self.cached_section_times[section] = timestamp

    def _build_effective_payload(self, payload: dict[str, object]) -> dict[str, object]:
        effective: dict[str, object] = {"_errors": payload.get("_errors", {})}
        for section in ("quote", "chart", "summary", "options"):
            if section in payload:
                effective[section] = payload[section]
                continue
            if section in self.cached_sections:
                effective[section] = self.cached_sections[section]
        return effective

    @staticmethod
    def _to_float(value: object) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _fmt_price(value: float | None) -> str:
        if value is None:
            return "--"
        return f"{value:.2f}"

    @staticmethod
    def _fmt_signed(value: float | None) -> str:
        if value is None:
            return "--"
        return f"{value:+.2f}"

    @staticmethod
    def _fmt_percent(value: float | None) -> str:
        if value is None:
            return "--"
        return f"{value:+.2f}%"

    @staticmethod
    def _fmt_compact(value: float | None) -> str:
        if value is None:
            return "--"
        num = float(value)
        abs_num = abs(num)
        if abs_num >= 1_000_000_000_000:
            return f"{num / 1_000_000_000_000:.2f}T"
        if abs_num >= 1_000_000_000:
            return f"{num / 1_000_000_000:.2f}B"
        if abs_num >= 1_000_000:
            return f"{num / 1_000_000:.2f}M"
        if abs_num >= 1_000:
            return f"{num / 1_000:.2f}K"
        return f"{num:.0f}"

    @staticmethod
    def _fmt_currency(value: float | None) -> str:
        if value is None:
            return "--"
        return f"${value:,.2f}"

    @staticmethod
    def _trim_error(message: str, limit: int = 80) -> str:
        clean = " ".join(str(message).split())
        if len(clean) <= limit:
            return clean
        return clean[: limit - 3] + "..."

    def _parse_date_any(self, raw: object) -> dt.datetime | None:
        if raw is None:
            return None
        text = str(raw).strip()
        if not text:
            return None

        iso_text = text
        if iso_text.endswith("Z"):
            iso_text = iso_text[:-1] + "+00:00"
        try:
            parsed_iso = dt.datetime.fromisoformat(iso_text)
            if parsed_iso.tzinfo is None:
                if "T" not in text and " " not in text:
                    parsed_iso = parsed_iso.replace(hour=16, minute=0, second=0, microsecond=0)
                return parsed_iso.replace(tzinfo=self.est_tz)
            return parsed_iso.astimezone(self.est_tz)
        except ValueError:
            pass

        formats: list[tuple[str, bool]] = [
            ("%Y-%m-%d %H:%M:%S", True),
            ("%Y-%m-%d %H:%M", True),
            ("%Y/%m/%d %H:%M:%S", True),
            ("%Y/%m/%d %H:%M", True),
            ("%m/%d/%Y %H:%M:%S", True),
            ("%m/%d/%Y %H:%M", True),
            ("%Y-%m-%d", False),
            ("%Y-%m", False),
            ("%Y/%m/%d", False),
            ("%m/%d/%Y", False),
        ]
        for fmt, has_time in formats:
            try:
                parsed = dt.datetime.strptime(text, fmt)
                if fmt == "%Y-%m":
                    parsed = parsed.replace(day=1)
                if not has_time:
                    parsed = parsed.replace(hour=16, minute=0, second=0, microsecond=0)
                return parsed.replace(tzinfo=self.est_tz)
            except ValueError:
                continue
        return None

    def _parse_stooq_quote_timestamp(self, date_text: object, time_text: object) -> dt.datetime | None:
        date_raw = str(date_text or "").strip()
        if not date_raw:
            return None

        time_raw = str(time_text or "").strip()
        if time_raw and time_raw.upper() not in {"N/D", "ND", "-"}:
            parsed = self._parse_date_any(f"{date_raw} {time_raw}")
            if parsed is not None:
                return parsed
        return self._parse_date_any(date_raw)

    def _append_price_point(self, ts: dt.datetime | None, price: float) -> None:
        if price <= 0:
            return
        now_est = dt.datetime.now(self.est_tz)
        if ts is None:
            ts = now_est
        elif ts.tzinfo is None:
            ts = ts.replace(tzinfo=self.est_tz)
        else:
            ts = ts.astimezone(self.est_tz)
        delta_seconds = (now_est - ts).total_seconds()
        if delta_seconds < -300 or delta_seconds > 72 * 3600:
            ts = now_est

        if self.price_history and ts <= self.price_history[-1][0]:
            last_ts, _last_price = self.price_history[-1]
            if ts == last_ts:
                self.price_history[-1] = (ts, price)
                return
            ts = last_ts + dt.timedelta(seconds=1)

        self.price_history.append((ts, price))
        if len(self.price_history) > 12000:
            self.price_history = self.price_history[-12000:]

    def _format_est_timestamp(self, ts: dt.datetime, include_year: bool = False) -> str:
        if ts.tzinfo is None:
            ts_est = ts.replace(tzinfo=self.est_tz)
        else:
            ts_est = ts.astimezone(self.est_tz)
        date_text = ts_est.strftime("%m/%d/%Y" if include_year else "%m/%d")
        time_text = ts_est.strftime("%I:%M %p").lstrip("0")
        return f"{date_text} {time_text} ET"

    def _format_label_for_mode(self, ts: dt.datetime | None, mode_id: str) -> str:
        if ts is None:
            return ""
        return self._format_est_timestamp(ts, include_year=mode_id in {"month", "year"})

    def _apply_graph_mode_to_points(
        self,
        points: list[tuple[dt.datetime | None, float]],
        mode: dict[str, object],
    ) -> list[tuple[dt.datetime | None, float]]:
        if not points:
            return points
        downsample = int(mode.get("downsample", 1))
        if downsample > 1 and len(points) > 1:
            sampled: list[tuple[dt.datetime | None, float]] = []
            for idx in range(downsample - 1, len(points), downsample):
                sampled.append(points[idx])
            if sampled[-1] != points[-1]:
                sampled.append(points[-1])
            points = sampled

        mode_id = str(mode.get("id", ""))
        if mode_id in {"week", "month"}:
            grouped: dict[tuple[int, int], tuple[dt.datetime | None, float]] = {}
            fallback = 0
            for ts, value in points:
                if ts is None:
                    key = (9999, fallback)
                    fallback += 1
                elif mode_id == "week":
                    iso = ts.isocalendar()
                    key = (iso.year, iso.week)
                else:
                    key = (ts.year, ts.month)
                grouped[key] = (ts, value)
            points = [grouped[key] for key in sorted(grouped.keys())]

        if bool(mode.get("year_aggregate", False)):
            by_year: dict[int, tuple[dt.datetime | None, float]] = {}
            fallback_year = 0
            for ts, value in points:
                if ts is not None:
                    year = ts.year
                else:
                    year = 10_000 + fallback_year
                    fallback_year += 1
                by_year[year] = (ts, value)
            points = [by_year[key] for key in sorted(by_year.keys())]
        return points

    def _extract_quote_metrics(
        self,
        quote_section: object,
    ) -> tuple[dict[str, str], str, float | None, float | None, dt.datetime | None]:
        metrics = {field: "--" for field in self.METRIC_FIELDS}
        quote_source = "n/a"

        if isinstance(quote_section, dict):
            td_quote = quote_section.get("twelveDataQuote")
            if isinstance(td_quote, dict):
                quote_source = "Twelve Data"
                name = td_quote.get("name") or td_quote.get("symbol") or self.symbol
                metrics["Name"] = str(name)

                def parse_percent(raw_value: object) -> float | None:
                    if raw_value is None:
                        return None
                    text = str(raw_value).strip().replace("%", "")
                    try:
                        return float(text)
                    except ValueError:
                        return None

                price = self._to_float(td_quote.get("close") or td_quote.get("price"))
                open_ = self._to_float(td_quote.get("open"))
                low = self._to_float(td_quote.get("low"))
                high = self._to_float(td_quote.get("high"))
                prev_close = self._to_float(td_quote.get("previous_close"))
                volume = self._to_float(td_quote.get("volume"))
                change = self._to_float(td_quote.get("change"))
                change_pct = parse_percent(td_quote.get("percent_change"))
                quote_ts = self._parse_date_any(td_quote.get("datetime"))

                metrics["Price"] = self._fmt_price(price)
                if change is not None and change_pct is not None:
                    metrics["Change"] = f"{self._fmt_signed(change)} ({self._fmt_percent(change_pct)})"
                else:
                    metrics["Change"] = self._fmt_signed(change)
                metrics["Open"] = self._fmt_price(open_)
                metrics["Day Range"] = (
                    f"{self._fmt_price(low)} - {self._fmt_price(high)}"
                    if low is not None and high is not None
                    else "--"
                )
                metrics["Prev Close"] = self._fmt_price(prev_close)
                metrics["Volume"] = self._fmt_compact(volume)
                metrics["Market Cap"] = "--"
                metrics["Exchange"] = str(td_quote.get("exchange") or "--")
                metrics["Currency"] = str(td_quote.get("currency") or "USD")
                metrics["Quote Source"] = quote_source
                reference_price = prev_close if prev_close is not None else open_
                return metrics, quote_source, price, reference_price, quote_ts

            quote_response = quote_section.get("quoteResponse")
            if isinstance(quote_response, dict):
                results = quote_response.get("result")
                if isinstance(results, list) and results:
                    first = results[0]
                    if isinstance(first, dict):
                        quote_source = "Yahoo"
                        name = first.get("longName") or first.get("shortName") or first.get("symbol")
                        metrics["Name"] = str(name) if name else self.symbol

                        price = self._to_float(first.get("regularMarketPrice"))
                        open_ = self._to_float(first.get("regularMarketOpen"))
                        low = self._to_float(first.get("regularMarketDayLow"))
                        high = self._to_float(first.get("regularMarketDayHigh"))
                        prev_close = self._to_float(first.get("regularMarketPreviousClose"))
                        volume = self._to_float(first.get("regularMarketVolume"))
                        market_cap = self._to_float(first.get("marketCap"))
                        change = self._to_float(first.get("regularMarketChange"))
                        change_pct = self._to_float(first.get("regularMarketChangePercent"))

                        metrics["Price"] = self._fmt_price(price)
                        if change is not None and change_pct is not None:
                            metrics["Change"] = f"{self._fmt_signed(change)} ({self._fmt_percent(change_pct)})"
                        else:
                            metrics["Change"] = self._fmt_signed(change)
                        metrics["Open"] = self._fmt_price(open_)
                        metrics["Day Range"] = (
                            f"{self._fmt_price(low)} - {self._fmt_price(high)}"
                            if low is not None and high is not None
                            else "--"
                        )
                        metrics["Prev Close"] = self._fmt_price(prev_close)
                        metrics["Volume"] = self._fmt_compact(volume)
                        metrics["Market Cap"] = self._fmt_compact(market_cap)
                        metrics["Exchange"] = str(first.get("fullExchangeName") or first.get("exchange") or "--")
                        metrics["Currency"] = str(first.get("currency") or "--")
                        metrics["Quote Source"] = quote_source
                        reference_price = prev_close if prev_close is not None else open_
                        return metrics, quote_source, price, reference_price, None

            if "fallbackQuote" in quote_section:
                quote_source = "Stooq fallback"
                fallback_quote = quote_section.get("fallbackQuote")
                close: float | None = None
                open_: float | None = None
                quote_ts: dt.datetime | None = None
                if isinstance(fallback_quote, dict):
                    symbol_value = fallback_quote.get("symbol")
                    if symbol_value:
                        metrics["Name"] = str(symbol_value).upper()
                    close = self._to_float(fallback_quote.get("close"))
                    open_ = self._to_float(fallback_quote.get("open"))
                    quote_ts = self._parse_stooq_quote_timestamp(
                        fallback_quote.get("date"),
                        fallback_quote.get("time"),
                    )
                    high = self._to_float(fallback_quote.get("high"))
                    low = self._to_float(fallback_quote.get("low"))
                    volume = self._to_float(fallback_quote.get("volume"))
                    metrics["Price"] = self._fmt_price(close)
                    metrics["Open"] = self._fmt_price(open_)
                    metrics["Day Range"] = (
                        f"{self._fmt_price(low)} - {self._fmt_price(high)}"
                        if low is not None and high is not None
                        else "--"
                    )
                    if close is not None and open_ is not None:
                        change = close - open_
                        pct = (change / open_) * 100 if open_ else None
                        metrics["Change"] = f"{self._fmt_signed(change)} ({self._fmt_percent(pct)})"
                    metrics["Prev Close"] = "--"
                    metrics["Volume"] = self._fmt_compact(volume)
                    metrics["Market Cap"] = "--"
                    metrics["Exchange"] = "Stooq feed"
                    metrics["Currency"] = "USD"
                metrics["Quote Source"] = quote_source
                return metrics, quote_source, close, open_, quote_ts

        metrics["Name"] = self.symbol
        metrics["Quote Source"] = quote_source
        return metrics, quote_source, None, None, None

    def _build_rolling_bars_from_points(
        self,
        points: list[tuple[dt.datetime, float]],
        mode: dict[str, object],
    ) -> tuple[list[float], list[str]]:
        if not points:
            return [], []

        mode_id = str(mode.get("id", ""))
        bar_count = 30
        window_seconds = max(60, int(mode.get("window_seconds", 30 * 60)))
        bucket_seconds = max(1, int(round(window_seconds / bar_count)))
        ordered = sorted(points, key=lambda item: item[0])
        now_est = dt.datetime.now(self.est_tz)
        latest_point_ts = ordered[-1][0]
        anchor_ts = now_est
        if (now_est - latest_point_ts).total_seconds() > bucket_seconds * 2:
            anchor_ts = latest_point_ts
        end_epoch = int(anchor_ts.timestamp())
        end_epoch -= end_epoch % bucket_seconds
        end_ts = dt.datetime.fromtimestamp(end_epoch, tz=self.est_tz)
        start_ts = end_ts - dt.timedelta(seconds=bucket_seconds * (bar_count - 1))

        idx = 0
        last_val: float | None = None
        while idx < len(ordered) and ordered[idx][0] <= start_ts:
            last_val = ordered[idx][1]
            idx += 1
        if last_val is None and ordered:
            last_val = ordered[0][1]

        values: list[float] = []
        labels: list[str] = []
        for i in range(bar_count):
            bucket_ts = start_ts + dt.timedelta(seconds=i * bucket_seconds)
            while idx < len(ordered) and ordered[idx][0] <= bucket_ts:
                last_val = ordered[idx][1]
                idx += 1
            if last_val is None:
                last_val = ordered[0][1]
            values.append(last_val)
            labels.append(self._format_label_for_mode(bucket_ts, mode_id))
        return values, labels

    def _extract_chart_values(self, chart_section: object) -> tuple[list[float], list[str], str]:
        mode = self._current_graph_mode()
        mode_id = str(mode.get("id", ""))
        source_points: list[tuple[dt.datetime, float]] = []
        source_parts: list[str] = []

        if isinstance(chart_section, dict):
            td_chart = chart_section.get("twelveDataChart")
            if isinstance(td_chart, dict):
                td_values = td_chart.get("values")
                td_interval = str(td_chart.get("interval") or "")
                parsed_any = False
                if isinstance(td_values, list):
                    for item in td_values:
                        if not isinstance(item, dict):
                            continue
                        value = self._to_float(item.get("close"))
                        parsed_ts = self._parse_date_any(item.get("datetime") or item.get("date"))
                        if value is None or parsed_ts is None:
                            continue
                        ts_est = parsed_ts.astimezone(self.est_tz) if parsed_ts.tzinfo else parsed_ts.replace(tzinfo=self.est_tz)
                        source_points.append((ts_est, value))
                        parsed_any = True
                if parsed_any:
                    source_parts.append(f"TwelveData i={td_interval}" if td_interval else "TwelveData")

            chart_obj = chart_section.get("chart")
            if isinstance(chart_obj, dict):
                result = chart_obj.get("result")
                if isinstance(result, list) and result:
                    first = result[0]
                    if isinstance(first, dict):
                        timestamps = first.get("timestamp")
                        indicators = first.get("indicators")
                        closes: list[object] = []
                        opens: list[object] = []
                        highs: list[object] = []
                        lows: list[object] = []
                        if isinstance(indicators, dict):
                            quote_sets = indicators.get("quote")
                            if isinstance(quote_sets, list) and quote_sets:
                                quote0 = quote_sets[0]
                                if isinstance(quote0, dict):
                                    raw_close = quote0.get("close")
                                    if isinstance(raw_close, list):
                                        closes = raw_close
                                    raw_open = quote0.get("open")
                                    if isinstance(raw_open, list):
                                        opens = raw_open
                                    raw_high = quote0.get("high")
                                    if isinstance(raw_high, list):
                                        highs = raw_high
                                    raw_low = quote0.get("low")
                                    if isinstance(raw_low, list):
                                        lows = raw_low
                        if isinstance(timestamps, list) and (closes or opens or highs or lows):
                            parsed_any = False
                            for idx, raw_ts in enumerate(timestamps):
                                candidates: list[object] = []
                                if idx < len(closes):
                                    candidates.append(closes[idx])
                                if idx < len(opens):
                                    candidates.append(opens[idx])
                                if idx < len(highs):
                                    candidates.append(highs[idx])
                                if idx < len(lows):
                                    candidates.append(lows[idx])
                                value: float | None = None
                                for candidate in candidates:
                                    value = self._to_float(candidate)
                                    if value is not None:
                                        break
                                if value is None:
                                    continue
                                try:
                                    parsed_ts = dt.datetime.fromtimestamp(float(raw_ts), tz=dt.timezone.utc).astimezone(self.est_tz)
                                except (TypeError, ValueError, OSError):
                                    continue
                                source_points.append((parsed_ts, value))
                                parsed_any = True
                            if parsed_any:
                                metadata = first.get("meta")
                                granularity = ""
                                if isinstance(metadata, dict):
                                    granularity = str(metadata.get("dataGranularity") or "")
                                source_parts.append(f"Yahoo i={granularity}" if granularity else "Yahoo")

            fallback_chart = chart_section.get("fallbackChart")
            if isinstance(fallback_chart, dict):
                chart_interval = str(fallback_chart.get("interval") or "n/a")
                bars = fallback_chart.get("bars")
                if isinstance(bars, list):
                    parsed_any = False
                    for item in bars:
                        if not isinstance(item, dict):
                            continue
                        value = self._to_float(item.get("close"))
                        parsed_ts = self._parse_stooq_quote_timestamp(item.get("date"), item.get("time"))
                        if parsed_ts is None:
                            parsed_ts = self._parse_date_any(item.get("date"))
                        if value is None or parsed_ts is None:
                            continue
                        ts_est = parsed_ts.astimezone(self.est_tz) if parsed_ts.tzinfo else parsed_ts.replace(tzinfo=self.est_tz)
                        source_points.append((ts_est, value))
                        parsed_any = True
                    if parsed_any:
                        source_parts.append(f"Stooq i={chart_interval}")

        using_live_only = not source_points
        all_points = list(source_points)
        for ts, price in self.price_history:
            ts_est = ts.astimezone(self.est_tz) if ts.tzinfo else ts.replace(tzinfo=self.est_tz)
            all_points.append((ts_est, price))
        if using_live_only and all_points:
            source_parts.append("Live quote")

        if not all_points:
            if self.graph_values:
                labels = self.graph_labels if self.graph_labels else ["" for _ in self.graph_values]
                return self.graph_values, labels, self.graph_source
            return [], [], "n/a"

        dedup: dict[dt.datetime, float] = {}
        for ts, price in all_points:
            dedup[ts] = price
        merged_points = sorted(dedup.items(), key=lambda item: item[0])

        now_est = dt.datetime.now(self.est_tz)
        merged_points = [item for item in merged_points if item[0] <= now_est + dt.timedelta(minutes=2)]
        if not merged_points:
            return [], [], "n/a"

        window_seconds = max(60, int(mode.get("window_seconds", 30 * 60)))
        cutoff = now_est - dt.timedelta(seconds=window_seconds)
        in_window_points = [item for item in merged_points if item[0] >= cutoff]
        if in_window_points:
            merged_points = in_window_points

        mode_points = self._apply_graph_mode_to_points([(ts, value) for ts, value in merged_points], mode)
        if not mode_points:
            return [], [], "n/a"

        values: list[float] = []
        labels: list[str] = []
        for ts, value in mode_points:
            if value is None:
                continue
            values.append(float(value))
            labels.append(self._format_label_for_mode(ts, mode_id))
        if not values:
            return [], [], "n/a"

        unique_count = len({round(v, 6) for v in values})
        if unique_count <= 1 and self.graph_values:
            previous_unique_count = len({round(v, 6) for v in self.graph_values})
            if previous_unique_count > 1:
                previous_labels = self.graph_labels if self.graph_labels else ["" for _ in self.graph_values]
                return self.graph_values, previous_labels, f"{self.graph_source} (cached)"

        unique_sources = list(dict.fromkeys(source_parts))
        source_text = " + ".join(unique_sources) if unique_sources else "n/a"
        return values, labels, f"{source_text} {mode['name']}"

    def _update_dashboard(
        self,
        payload: dict[str, object],
        elapsed: float,
        include_heavy: bool,
        errors: dict[str, str],
    ) -> None:
        effective_payload = self._build_effective_payload(payload)
        quote_metrics, quote_source, current_price, reference_price, quote_timestamp = self._extract_quote_metrics(
            effective_payload.get("quote")
        )
        if current_price is not None and current_price > 0:
            self._append_price_point(quote_timestamp, current_price)
        chart_values, chart_labels, chart_source = self._extract_chart_values(effective_payload.get("chart"))

        self.graph_values = chart_values
        self.graph_labels = chart_labels
        self.graph_source = chart_source
        self.selection_start_idx = None
        self.selection_end_idx = None
        self.selection_dragging = False
        self.selection_summary = ""
        quote_metrics["Chart Source"] = chart_source
        quote_metrics["Quote Source"] = quote_source

        if current_price is not None and current_price > 0 and self.investment_amount > 0:
            if self.estimated_shares is None or self.estimated_shares <= 0:
                self.estimated_shares = self.investment_amount / current_price
            position_value = self.estimated_shares * current_price
            quote_metrics["Position Value"] = self._fmt_currency(position_value)
            quote_metrics["Est Shares"] = f"{self.estimated_shares:.6f}"
            if reference_price is not None:
                day_change = (current_price - reference_price) * self.estimated_shares
                start_value = reference_price * self.estimated_shares
                day_pct = (day_change / start_value * 100.0) if start_value else None
                quote_metrics["Today P/L"] = f"{self._fmt_currency(day_change)} ({self._fmt_percent(day_pct)})"
            else:
                quote_metrics["Today P/L"] = "--"
        else:
            quote_metrics["Position Value"] = "--"
            quote_metrics["Today P/L"] = "--"
            quote_metrics["Est Shares"] = "--"

        for field in self.METRIC_FIELDS:
            self.metric_vars[field].set(quote_metrics.get(field, "--"))

        if errors:
            short_warnings = " | ".join(
                f"{key}: {self._trim_error(str(errors[key]))}"
                for key in sorted(errors.keys())
            )
            self.warnings_value.configure(text=short_warnings, fg=ERROR_COLOR)
        else:
            self.warnings_value.configure(text="none", fg=DIM_TEXT_COLOR)

        now_text = dt.datetime.now(self.est_tz).strftime("%Y-%m-%d %H:%M:%S ET")
        if errors:
            self.status_label.configure(
                text=(
                    f"Updated {now_text} in {elapsed:.2f}s | warnings={len(errors)} | "
                    f"next refresh in {self.dynamic_refresh_seconds}s"
                ),
                fg=ERROR_COLOR,
            )
        else:
            self.status_label.configure(
                text=f"Updated {now_text} in {elapsed:.2f}s | next refresh in {self.dynamic_refresh_seconds}s",
                fg=DIM_TEXT_COLOR,
            )

        mode_text = "FULL" if include_heavy else "FAST"
        graph_mode_name = str(self._current_graph_mode()["name"])
        invest_text = self._fmt_currency(self.investment_amount)
        self.bottom_base_text = (
            f"Mode={mode_text} | Graph={graph_mode_name} | Invested={invest_text} | "
            f"Quote={quote_source} | Chart={chart_source} | "
            "R=refresh F=full S=stock G=graph mode T=refresh sec Q=quit"
        )
        self.bottom_label.configure(text=self.bottom_base_text, fg=DIM_TEXT_COLOR)
        self.graph_title_label.configure(text=f"PRICE BAR GRAPH ({graph_mode_name} | {chart_source})")
        self._redraw_graph()

    def _redraw_graph(self) -> None:
        if not hasattr(self, "graph_canvas"):
            return
        canvas = self.graph_canvas
        canvas.delete("all")
        self.graph_bars = []
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 120 or height < 120:
            return

        x0 = 58
        y0 = 18
        x1 = width - 18
        y1 = height - 34
        self.graph_plot_bounds = (x0, y0, x1, y1)
        canvas.create_rectangle(x0, y0, x1, y1, outline="#1E3A2A", width=1)

        if not self.graph_values:
            canvas.create_text(
                width / 2,
                height / 2,
                text="Waiting for chart data...",
                fill=DIM_TEXT_COLOR,
                font=self.text_font,
            )
            self.selection_start_idx = None
            self.selection_end_idx = None
            self.selection_dragging = False
            self.selection_summary = ""
            return

        max_bars = max(20, int((x1 - x0) / 5))
        values = self.graph_values[-max_bars:]
        labels = self.graph_labels[-len(values):] if self.graph_labels else ["" for _ in values]
        if len(labels) < len(values):
            labels = [""] * (len(values) - len(labels)) + labels
        min_v = min(values)
        max_v = max(values)
        spread = max_v - min_v
        if spread <= 1e-9:
            spread = max(0.01, abs(max_v) * 0.01 + 0.01)
            min_v -= spread / 2
            max_v += spread / 2

        bar_w = (x1 - x0) / max(1, len(values))
        for index, value in enumerate(values):
            normalized = (value - min_v) / (max_v - min_v)
            bar_top = y1 - normalized * (y1 - y0)
            left = x0 + index * bar_w + 0.5
            right = x0 + (index + 1) * bar_w - 1.0
            if right <= left:
                right = left + 1.0
            color = APPLE_1984_COLORS[(index + self.refresh_count) % len(APPLE_1984_COLORS)]
            canvas.create_rectangle(left, bar_top, right, y1, fill=color, outline="")
            self.graph_bars.append(
                {
                    "index": index,
                    "left": left,
                    "right": right,
                    "value": value,
                    "label": labels[index] if index < len(labels) else "",
                }
            )

        last = values[-1]
        last_y = y1 - ((last - min_v) / (max_v - min_v)) * (y1 - y0)
        canvas.create_line(x0, last_y, x1, last_y, fill=TEXT_COLOR, dash=(3, 2))

        canvas.create_text(
            8,
            y0,
            anchor="w",
            text=self._fmt_price(max_v),
            fill=DIM_TEXT_COLOR,
            font=self.text_font,
        )
        canvas.create_text(
            8,
            y1,
            anchor="w",
            text=self._fmt_price(min_v),
            fill=DIM_TEXT_COLOR,
            font=self.text_font,
        )
        canvas.create_text(
            x1 - 6,
            y0 + 6,
            anchor="ne",
            text=f"Last {self._fmt_price(last)}",
            fill=TEXT_COLOR,
            font=self.text_font,
        )
        canvas.create_text(
            x1 - 6,
            y1 + 14,
            anchor="se",
            text=f"{len(values)} bars",
            fill=DIM_TEXT_COLOR,
            font=self.text_font,
        )
        self._draw_selection_overlay()

    def _compose_bottom_text(self, extra: str | None = None) -> str:
        parts = [self.bottom_base_text]
        if self.selection_summary:
            parts.append(self.selection_summary)
        if extra:
            parts.append(extra)
        return " | ".join(part for part in parts if part)

    def _bar_index_for_x(self, x: float) -> int | None:
        for idx, bar in enumerate(self.graph_bars):
            if float(bar["left"]) <= x <= float(bar["right"]):
                return idx
        return None

    def _nearest_bar_index_for_x(self, x: float) -> int | None:
        if not self.graph_bars:
            return None
        best_idx = 0
        best_distance = float("inf")
        for idx, bar in enumerate(self.graph_bars):
            center = (float(bar["left"]) + float(bar["right"])) / 2.0
            distance = abs(center - x)
            if distance < best_distance:
                best_distance = distance
                best_idx = idx
        return best_idx

    def _draw_selection_overlay(self) -> None:
        if not hasattr(self, "graph_canvas"):
            return
        self.graph_canvas.delete("selection")
        if not self.graph_bars:
            self.selection_summary = ""
            return
        if self.selection_start_idx is None or self.selection_end_idx is None:
            self.selection_summary = ""
            return
        start_idx = max(0, min(len(self.graph_bars) - 1, self.selection_start_idx))
        end_idx = max(0, min(len(self.graph_bars) - 1, self.selection_end_idx))
        lo = min(start_idx, end_idx)
        hi = max(start_idx, end_idx)
        if lo >= len(self.graph_bars) or hi >= len(self.graph_bars):
            self.selection_summary = ""
            return
        start_bar = self.graph_bars[start_idx]
        end_bar = self.graph_bars[end_idx]
        start_value = self._to_float(start_bar.get("value"))
        end_value = self._to_float(end_bar.get("value"))
        if start_value is None or end_value is None:
            self.selection_summary = ""
            return

        delta = end_value - start_value
        pct = (delta / start_value * 100.0) if start_value else None
        selection_color = "#6CFF8A" if delta >= 0 else "#FF6E6E"
        pct_text = self._fmt_percent(pct)
        delta_text = self._fmt_signed(delta)
        start_label = str(start_bar.get("label") or f"#{lo + 1}")
        end_label = str(end_bar.get("label") or f"#{hi + 1}")
        self.selection_summary = f"Selection {start_label} -> {end_label}: {delta_text} ({pct_text})"

        x_left = float(start_bar["left"])
        x_right = float(end_bar["right"])
        _x0, y0, _x1, y1 = self.graph_plot_bounds
        self.graph_canvas.create_rectangle(
            x_left,
            y0,
            x_right,
            y1,
            outline=selection_color,
            width=2,
            tags="selection",
        )
        mid_x = (x_left + x_right) / 2.0
        self.graph_canvas.create_text(
            mid_x,
            y0 + 4,
            anchor="n",
            text=f"{delta_text} ({pct_text})",
            fill=selection_color,
            font=self.text_font,
            tags="selection",
        )
        self.bottom_label.configure(
            text=self._compose_bottom_text(),
            fg=DIM_TEXT_COLOR,
        )

    def _on_graph_leave(self, _event: tk.Event) -> None:
        if not hasattr(self, "graph_canvas"):
            return
        self.graph_canvas.delete("hover")
        self.bottom_label.configure(text=self._compose_bottom_text(), fg=DIM_TEXT_COLOR)

    def _on_graph_press(self, event: tk.Event) -> None:
        idx = self._bar_index_for_x(event.x)
        if idx is None:
            idx = self._nearest_bar_index_for_x(event.x)
        if idx is None:
            return
        self.selection_start_idx = idx
        self.selection_end_idx = idx
        self.selection_dragging = True
        self._draw_selection_overlay()

    def _on_graph_drag(self, event: tk.Event) -> None:
        if not self.selection_dragging:
            return
        idx = self._nearest_bar_index_for_x(event.x)
        if idx is None:
            return
        self.selection_end_idx = idx
        self._draw_selection_overlay()

    def _on_graph_release(self, event: tk.Event) -> None:
        if not self.selection_dragging:
            return
        idx = self._nearest_bar_index_for_x(event.x)
        if idx is not None:
            self.selection_end_idx = idx
        self.selection_dragging = False
        self._draw_selection_overlay()

    def _on_graph_motion(self, event: tk.Event) -> None:
        if self.selection_dragging:
            return
        if not self.graph_bars:
            self._on_graph_leave(event)
            return
        target: dict[str, object] | None = None
        for bar in self.graph_bars:
            left = float(bar["left"])
            right = float(bar["right"])
            if left <= event.x <= right:
                target = bar
                break
        if target is None:
            self._on_graph_leave(event)
            return

        value = self._to_float(target.get("value"))
        label = str(target.get("label") or "").strip()
        center_x = (float(target["left"]) + float(target["right"])) / 2
        _, y0, _, _ = self.graph_plot_bounds

        self.graph_canvas.delete("hover")
        self.graph_canvas.create_line(
            center_x,
            y0,
            center_x,
            self.graph_plot_bounds[3],
            fill=TEXT_COLOR,
            dash=(2, 2),
            tags="hover",
        )
        tooltip_text = self._fmt_price(value)
        if label:
            tooltip_text = f"{label}  {tooltip_text}"
        self.graph_canvas.create_text(
            min(self.graph_canvas.winfo_width() - 10, center_x + 6),
            max(12, y0 + 2),
            anchor="nw",
            text=tooltip_text,
            fill=TEXT_COLOR,
            font=self.text_font,
            tags="hover",
        )
        self.bottom_label.configure(
            text=self._compose_bottom_text(f"Hover: {tooltip_text}"),
            fg=DIM_TEXT_COLOR,
        )

    def _apply_refresh(
        self,
        payload: dict[str, object],
        elapsed: float,
        include_heavy: bool,
        refresh_started_epoch: float,
        refresh_id: int,
        symbol_snapshot: str,
    ) -> None:
        if refresh_id != self.refresh_id or symbol_snapshot != self.symbol:
            return
        self.refresh_count += 1
        self._cycle_logo_colors()
        self._update_cache(payload, refresh_started_epoch)

        errors = payload.get("_errors", {})
        if include_heavy:
            self.next_full_refresh_epoch = time.time() + self.full_refresh_seconds
        self.force_full_next = False
        self.dynamic_refresh_seconds = self.refresh_seconds

        if isinstance(errors, dict):
            self._update_dashboard(payload, elapsed, include_heavy, errors)
        else:
            self._update_dashboard(payload, elapsed, include_heavy, {})

        self.in_flight = False
        self._schedule_refresh()

    def _cycle_logo_colors(self) -> None:
        offset = self.refresh_count % len(APPLE_1984_COLORS)
        for index, label in enumerate(self.logo_labels):
            base_index = self.logo_base_color_indices[index] if index < len(self.logo_base_color_indices) else 0
            color = APPLE_1984_COLORS[(base_index + offset) % len(APPLE_1984_COLORS)]
            label.configure(fg=color)


def prompt_for_symbol(default_symbol: str = "AAPL") -> str:
    raw = input(f"Ticker symbol [{default_symbol}]: ").strip().upper()
    return raw or default_symbol


def prompt_for_logo_side(default_side: str = "right") -> str:
    prompt = "Logo corner (left/right) [right]: " if default_side == "right" else "Logo corner (left/right) [left]: "
    while True:
        raw = input(prompt).strip()
        if not raw:
            return default_side
        normalized = normalize_logo_side(raw)
        if normalized:
            return normalized
        print("Please enter left or right.")


def prompt_for_investment_amount(default_amount: float = 0.0) -> float:
    prompt = f"Total amount currently invested (USD) [{default_amount:.2f}]: "
    while True:
        raw = input(prompt).strip()
        if not raw:
            return default_amount
        parsed = parse_investment_amount_text(raw)
        if parsed is not None:
            return parsed
        print("Please enter a non-negative number, e.g. 12500 or 12500.50")


def prompt_for_twelvedata_key(default_key: str = "") -> str:
    prompt = "Twelve Data API key (optional, press Enter to skip): "
    raw = input(prompt).strip()
    return raw or default_key


def prompt_for_symbol_dialog(parent: tk.Tk, default_symbol: str = "AAPL") -> str:
    while True:
        raw = simpledialog.askstring(
            "Ticker Symbol",
            "Ticker symbol (for example AAPL):",
            parent=parent,
            initialvalue=default_symbol,
        )
        if raw is None:
            return default_symbol
        symbol = raw.strip().upper()
        if symbol:
            return symbol


def prompt_for_logo_side_dialog(parent: tk.Tk, default_side: str = "right") -> str:
    while True:
        raw = simpledialog.askstring(
            "Logo Corner",
            "Logo corner placement (left/right):",
            parent=parent,
            initialvalue=default_side,
        )
        if raw is None:
            return default_side
        normalized = normalize_logo_side(raw)
        if normalized:
            return normalized


def prompt_for_investment_amount_dialog(parent: tk.Tk, default_amount: float = 0.0) -> float:
    initial_value = f"{default_amount:.2f}"
    while True:
        raw = simpledialog.askstring(
            "Investment Amount",
            "Total amount currently invested in this symbol/index fund (USD):",
            parent=parent,
            initialvalue=initial_value,
        )
        if raw is None:
            return default_amount
        parsed = parse_investment_amount_text(raw)
        if parsed is not None:
            return parsed


def prompt_for_twelvedata_key_dialog(parent: tk.Tk, default_key: str = "") -> str:
    raw = simpledialog.askstring(
        "Twelve Data API Key",
        "Enter Twelve Data API key (optional, leave blank to skip):",
        parent=parent,
        initialvalue=default_key,
        show="*",
    )
    if raw is None:
        return default_key
    return raw.strip() or default_key


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="1984-style stock terminal.")
    parser.add_argument("symbol", nargs="?", help="Ticker symbol, for example AAPL")
    parser.add_argument(
        "--logo-side",
        dest="logo_side",
        choices=["left", "right"],
        help="Logo corner placement",
    )
    parser.add_argument(
        "--refresh",
        type=int,
        default=30,
        help="Refresh interval in seconds (minimum 5, default 30)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=12,
        help="Network timeout in seconds (default 12)",
    )
    parser.add_argument(
        "--full-refresh",
        type=int,
        default=900,
        help="How often to refetch heavy endpoints (summary/options) in seconds (default 900)",
    )
    parser.add_argument(
        "--amount",
        dest="amount",
        help="Total amount currently invested in this symbol/index fund (USD)",
    )
    parser.add_argument(
        "--twelvedata-key",
        dest="twelvedata_key",
        help="Twelve Data API key (free tier supported)",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    is_tty = sys.stdin.isatty()
    root = tk.Tk()
    root.withdraw()

    if args.symbol:
        symbol = args.symbol.strip().upper()
    elif is_tty:
        symbol = prompt_for_symbol()
    else:
        symbol = prompt_for_symbol_dialog(root, default_symbol="AAPL")

    if args.logo_side:
        logo_side = args.logo_side
    elif is_tty:
        logo_side = prompt_for_logo_side(default_side="right")
    else:
        logo_side = prompt_for_logo_side_dialog(root, default_side="right")

    if args.amount is not None:
        parsed_amount = parse_investment_amount_text(str(args.amount))
        if parsed_amount is None:
            print("Invalid --amount value. Use a non-negative number.")
            root.destroy()
            return 1
        investment_amount = parsed_amount
    elif is_tty:
        investment_amount = prompt_for_investment_amount(default_amount=0.0)
    else:
        investment_amount = prompt_for_investment_amount_dialog(root, default_amount=0.0)

    if args.twelvedata_key is not None:
        twelvedata_key = str(args.twelvedata_key).strip()
    elif is_tty:
        twelvedata_key = prompt_for_twelvedata_key(default_key="")
    else:
        twelvedata_key = prompt_for_twelvedata_key_dialog(root, default_key="")

    if not symbol:
        print("A ticker symbol is required.")
        root.destroy()
        return 1

    root.deiconify()
    StockTerminalApp(
        root=root,
        symbol=symbol,
        logo_side=logo_side,
        investment_amount=investment_amount,
        refresh_seconds=args.refresh,
        timeout_seconds=args.timeout,
        full_refresh_seconds=args.full_refresh,
        twelvedata_api_key=twelvedata_key,
    )
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
