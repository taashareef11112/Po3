#!/usr/bin/env python3
"""
KuCoin Low-Cap Sudden Liquidity Hunter
-------------------------------------
يراقب أزواج USDT في KuCoin ويكتشف تدفقات سيولة مفاجئة للعملات منخفضة الماركت كاب
(5M$ وأقل) مع فلترة ذكية لتقليل الإشارات العشوائية.

المزايا:
- بدون نظام Scores
- فلترة متعددة الشروط (سيولة + سعر + زخم + سبريد)
- تنبيهات تيليجرام عربية
"""

from __future__ import annotations

import asyncio
import logging
import os
import statistics
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests

KUCOIN_BASE = "https://api.kucoin.com"
TELEGRAM_BASE = "https://api.telegram.org"

# =========================
# الإعدادات
# =========================
MARKET_CAP_MAX_USD = 5_000_000
MIN_BASE_VOLUME_24H = 50_000
MAX_SPREAD_PCT = 0.70
COOLDOWN_SECONDS = 60 * 20
POLL_SECONDS = 25
LOOKBACK = 10

# شروط الإشارة الذكية (بدون سكور)
MIN_VOLUME_SPIKE_RATIO = 2.8
MIN_PRICE_MOVE_PCT_5M = 2.2
MIN_TRADE_COUNT_SPIKE = 2.0
MIN_USDT_FLOW_5M = 18_000


@dataclass
class SymbolSnapshot:
    ts: float
    price: float
    vol_24h: float
    vol_value_24h: float
    spread_pct: float


class KuCoinLiquidityHunter:
    def __init__(self, telegram_token: str, chat_id: str):
        self.telegram_token = telegram_token
        self.chat_id = chat_id
        self.history: Dict[str, List[SymbolSnapshot]] = {}
        self.cooldown: Dict[str, float] = {}
        self.symbol_meta: Dict[str, dict] = {}
        self.session = requests.Session()

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        url = f"{KUCOIN_BASE}{path}"
        r = self.session.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "200000":
            raise RuntimeError(f"KuCoin API error: {data}")
        return data["data"]

    def _send_telegram(self, message: str) -> None:
        url = f"{TELEGRAM_BASE}/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        r = self.session.post(url, json=payload, timeout=10)
        r.raise_for_status()

    def load_symbols(self) -> None:
        all_symbols = self._get("/api/v2/symbols")
        filtered = {}
        for s in all_symbols:
            if s.get("quoteCurrency") != "USDT":
                continue
            if s.get("enableTrading") is not True:
                continue
            filtered[s["symbol"]] = s
        self.symbol_meta = filtered
        logging.info("Loaded %s USDT tradable symbols", len(self.symbol_meta))

    def fetch_market_caps(self) -> Dict[str, float]:
        # CoinGecko mapping by symbol name for approximate cap filtering.
        markets = self.session.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_asc",
                "per_page": 250,
                "page": 1,
                "sparkline": "false",
            },
            timeout=12,
        )
        markets.raise_for_status()
        caps = {}
        for item in markets.json():
            sym = str(item.get("symbol", "")).upper()
            cap = item.get("market_cap")
            if cap is not None:
                caps[sym] = float(cap)
        return caps

    def fetch_ticker(self) -> List[dict]:
        return self._get("/api/v1/market/allTickers")["ticker"]

    @staticmethod
    def _safe_float(v: str) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    def _is_low_cap(self, base_symbol: str, cap_map: Dict[str, float]) -> bool:
        cap = cap_map.get(base_symbol.upper())
        return cap is not None and cap <= MARKET_CAP_MAX_USD

    def process(self, ticker_rows: List[dict], cap_map: Dict[str, float]) -> List[Tuple[str, dict]]:
        hits = []
        now = time.time()

        for row in ticker_rows:
            symbol = row.get("symbol")
            if symbol not in self.symbol_meta:
                continue

            base = symbol.split("-")[0]
            if not self._is_low_cap(base, cap_map):
                continue

            last = self._safe_float(row.get("last"))
            buy = self._safe_float(row.get("buy"))
            sell = self._safe_float(row.get("sell"))
            vol = self._safe_float(row.get("vol"))
            vol_value = self._safe_float(row.get("volValue"))

            if last <= 0 or buy <= 0 or sell <= 0:
                continue

            spread_pct = ((sell - buy) / max(last, 1e-12)) * 100
            if spread_pct > MAX_SPREAD_PCT:
                continue

            if vol_value < MIN_BASE_VOLUME_24H:
                continue

            snap = SymbolSnapshot(
                ts=now,
                price=last,
                vol_24h=vol,
                vol_value_24h=vol_value,
                spread_pct=spread_pct,
            )
            arr = self.history.setdefault(symbol, [])
            arr.append(snap)
            if len(arr) > LOOKBACK:
                arr.pop(0)

            if len(arr) < 6:
                continue

            if now - self.cooldown.get(symbol, 0) < COOLDOWN_SECONDS:
                continue

            old = arr[0]
            recent = arr[-1]

            price_move_pct = ((recent.price - old.price) / max(old.price, 1e-12)) * 100
            usdt_flow_5m = max(recent.vol_value_24h - old.vol_value_24h, 0)

            vols = [x.vol_value_24h for x in arr[:-1]]
            baseline = statistics.mean(vols) if vols else 0
            volume_spike_ratio = (recent.vol_value_24h / baseline) if baseline > 0 else 0

            trade_count_spike = (
                (self._safe_float(row.get("takerFeeRate")) + 1e-9)
                / (self._safe_float(row.get("makerFeeRate")) + 1e-9)
            )

            # خوارزمية شرطية متعددة (بدون سكور)
            if (
                volume_spike_ratio >= MIN_VOLUME_SPIKE_RATIO
                and price_move_pct >= MIN_PRICE_MOVE_PCT_5M
                and trade_count_spike >= MIN_TRADE_COUNT_SPIKE
                and usdt_flow_5m >= MIN_USDT_FLOW_5M
            ):
                hit = {
                    "symbol": symbol,
                    "base": base,
                    "price": recent.price,
                    "price_move_pct": price_move_pct,
                    "volume_spike_ratio": volume_spike_ratio,
                    "usdt_flow_5m": usdt_flow_5m,
                    "spread_pct": recent.spread_pct,
                    "market_cap": cap_map.get(base.upper()),
                }
                hits.append((symbol, hit))
                self.cooldown[symbol] = now

        return hits

    def format_alert(self, hit: dict) -> str:
        return (
            "🚨 <b>تنبيه سيولة مفاجئة (Low Cap)</b>\n"
            f"الزوج: <b>{hit['symbol']}</b>\n"
            f"السعر: <b>{hit['price']:.8f}</b>\n"
            f"تغير 5m: <b>{hit['price_move_pct']:.2f}%</b>\n"
            f"قفزة الحجم: <b>{hit['volume_spike_ratio']:.2f}x</b>\n"
            f"تدفق USDT: <b>{hit['usdt_flow_5m']:.0f}$</b>\n"
            f"السبريد: <b>{hit['spread_pct']:.3f}%</b>\n"
            f"الماركت كاب: <b>{hit['market_cap']:.0f}$</b>\n"
            "\n⚠️ تأكيد يدوي مطلوب قبل الدخول."
        )

    async def run(self) -> None:
        self.load_symbols()
        self._send_telegram("✅ تم تشغيل بوت KuCoin لصيد السيولة المنخفضة بنجاح.")

        while True:
            try:
                cap_map = self.fetch_market_caps()
                ticker = self.fetch_ticker()
                hits = self.process(ticker, cap_map)

                for _, hit in hits:
                    msg = self.format_alert(hit)
                    self._send_telegram(msg)
                    logging.info("Alert sent for %s", hit["symbol"])

            except Exception as e:
                logging.exception("Loop error")
                try:
                    self._send_telegram(f"❌ خطأ في البوت: {e}")
                except Exception:
                    pass

            await asyncio.sleep(POLL_SECONDS)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    telegram_token = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if not telegram_token or not chat_id:
        raise SystemExit("Please set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID as environment variables.")

    bot = KuCoinLiquidityHunter(telegram_token=telegram_token, chat_id=chat_id)
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
