#!/usr/bin/env python3
# alt_signals.py
import os
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone

MARKET_CHART_URL = "https://api.coingecko.com/api/v3/coins/{id}/market_chart"

# symbol -> id de CoinGecko
PAIRS = {
    "ETHBTC": "ethereum",
    "SOLBTC": "solana",
    "BNBBTC": "binancecoin",
    "XRPBTC": "ripple",
    "DOGEBTC": "dogecoin",
    "TRXBTC": "tron",
    "LINKBTC": "chainlink",
    "HYPEBTC": "hyperliquid",
    "PAXGBTC": "pax-gold",
}

RSI_OVERBOUGHT = 75
RSI_OVERSOLD = 25


def cg_headers():
    api_key = os.environ.get("COINGECKO_API_KEY")
    return {"x-cg-demo-api-key": api_key} if api_key else {}


def get_market_chart_btc(coin_id, days=365):
    """Precios del par en BTC (vs_currency=btc), ultimos 'days' dias."""
    url = MARKET_CHART_URL.format(id=coin_id)
    params = urllib.parse.urlencode({"vs_currency": "btc", "days": days})
    full_url = f"{url}?{params}"
    req = urllib.request.Request(full_url, headers=cg_headers())
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode())
    return data["prices"]


def group_last(prices, keyfunc):
    groups = {}
    order = []
    for ts, price in prices:
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        key = keyfunc(dt)
        if key not in groups:
            order.append(key)
        groups[key] = price
    return [groups[k] for k in order]


def compute_rsi(closes, period=14):
    if len(closes) < 3:
        return None
    period = min(period, len(closes) - 1)
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def zone_label(rsi):
    if rsi is None:
        return None
    if rsi >= RSI_OVERBOUGHT:
        return "⚠️ sobrecompra"
    if rsi <= RSI_OVERSOLD:
        return "⚠️ sobreventa"
    return None


def send_telegram(msg):
    token = os.environ.get("TELEGRAM_BOT_TOKEN_ALT")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": msg}).encode()
    urllib.request.urlopen(url, data=data, timeout=10)


def main():
    for symbol, coin_id in PAIRS.items():
        try:
            prices = get_market_chart_btc(coin_id, days=365)
        except Exception as e:
            print(f"Aviso: no se pudo obtener {symbol} ({e})")
            continue

        daily_closes = [p for _, p in prices]
        weekly_closes = group_last(prices, lambda dt: (dt.isocalendar()[0], dt.isocalendar()[1]))

        rsi_daily = compute_rsi(daily_closes)
        rsi_weekly = compute_rsi(weekly_closes)

        flag_daily = zone_label(rsi_daily)
        flag_weekly = zone_label(rsi_weekly)

        if flag_daily is None and flag_weekly is None:
            rd = f"{rsi_daily:.0f}" if rsi_daily is not None else "N/A"
            rw = f"{rsi_weekly:.0f}" if rsi_weekly is not None else "N/A"
            print(f"{symbol}: sin zona extrema (RSI diario {rd}, semanal {rw})")
            continue

        lines = [f"🔔 {symbol}"]
        if flag_daily:
            lines.append(f"RSI diario: {rsi_daily:.0f} {flag_daily}")
        if flag_weekly:
            lines.append(f"RSI semanal: {rsi_weekly:.0f} {flag_weekly}")

        msg = "\n".join(lines)
        print(msg)
        send_telegram(msg)


if __name__ == "__main__":
    main()
