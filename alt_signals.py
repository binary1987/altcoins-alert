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


def compute_rsi_series(closes, period=14):
    if len(closes) < period + 2:
        return []

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def rsi_value(ag, al):
        if al == 0:
            return 100.0
        rs = ag / al
        return 100 - (100 / (1 + rs))

    rsis = [rsi_value(avg_gain, avg_loss)]
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsis.append(rsi_value(avg_gain, avg_loss))

    return rsis


def find_extrema(values, order, min_distance):
    peaks = []
    troughs = []
    n = len(values)
    i = order
    while i < n - order:
        window_before = values[max(0, i - order):i]
        window_after = values[i + 1:i + 1 + order]

        if window_before and window_after:
            is_peak = all(values[i] >= v for v in window_before) and all(values[i] >= v for v in window_after)
            is_trough = all(values[i] <= v for v in window_before) and all(values[i] <= v for v in window_after)

            if is_peak:
                peaks.append(i)
                i += min_distance
                continue
            if is_trough:
                troughs.append(i)
                i += min_distance
                continue
        i += 1

    return peaks, troughs


def detect_divergence(closes, order=3, min_distance=5, rsi_period=14):
    """Misma logica que en report.py: compara los 2 ultimos picos/valles
    de precio contra los de RSI para detectar divergencia."""
    rsi_series = compute_rsi_series(closes, period=rsi_period)
    if len(rsi_series) < order * 2 + min_distance + 2:
        return None

    aligned_closes = closes[-len(rsi_series):]
    peaks, troughs = find_extrema(aligned_closes, order, min_distance)

    bearish = False
    if len(peaks) >= 2:
        i1, i2 = peaks[-2], peaks[-1]
        if aligned_closes[i2] > aligned_closes[i1] and rsi_series[i2] < rsi_series[i1]:
            bearish = True

    bullish = False
    if len(troughs) >= 2:
        i1, i2 = troughs[-2], troughs[-1]
        if aligned_closes[i2] < aligned_closes[i1] and rsi_series[i2] > rsi_series[i1]:
            bullish = True

    if bearish and bullish:
        return None  # señales mixtas, no alertamos para evitar ruido
    if bearish:
        return "bajista"
    if bullish:
        return "alcista"
    return None


def zone_info(rsi):
    """Devuelve (etiqueta, emoji_color) o (None, None) si no esta en zona extrema."""
    if rsi is None:
        return None, None
    if rsi >= RSI_OVERBOUGHT:
        return "sobrecompra", "🔴"
    if rsi <= RSI_OVERSOLD:
        return "sobreventa", "🟢"
    return None, None


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

        label_daily, color_daily = zone_info(rsi_daily)
        label_weekly, color_weekly = zone_info(rsi_weekly)

        div_daily = detect_divergence(daily_closes, order=3, min_distance=5)
        div_weekly = detect_divergence(weekly_closes, order=2, min_distance=3)

        if label_daily is None and label_weekly is None and div_daily is None and div_weekly is None:
            rd = f"{rsi_daily:.0f}" if rsi_daily is not None else "N/A"
            rw = f"{rsi_weekly:.0f}" if rsi_weekly is not None else "N/A"
            print(f"{symbol}: sin señales (RSI diario {rd}, semanal {rw})")
            continue

        if label_daily:
            msg = f"🔔{color_daily} {symbol} — {label_daily}\nRSI diario: {rsi_daily:.0f}"
            print(msg)
            send_telegram(msg)

        if label_weekly:
            msg = f"🔔{color_weekly} {symbol} — {label_weekly}\nRSI semanal: {rsi_weekly:.0f}"
            print(msg)
            send_telegram(msg)

        if div_daily:
            color = "🟢" if div_daily == "alcista" else "🔴"
            msg = f"🔔{color} {symbol} — divergencia {div_daily} (diario)"
            print(msg)
            send_telegram(msg)

        if div_weekly:
            color = "🟢" if div_weekly == "alcista" else "🔴"
            msg = f"🔔{color} {symbol} — divergencia {div_weekly} (semanal)"
            print(msg)
            send_telegram(msg)


if __name__ == "__main__":
    main()
