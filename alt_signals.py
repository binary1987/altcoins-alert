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

PAIR_NAMES = {
    "ETHBTC": "Ethereum",
    "SOLBTC": "Solana",
    "BNBBTC": "BNB",
    "XRPBTC": "XRP",
    "DOGEBTC": "Dogecoin",
    "TRXBTC": "TRON",
    "LINKBTC": "Chainlink",
    "HYPEBTC": "Hyperliquid",
    "PAXGBTC": "PAX Gold",
}

RSI_OVERBOUGHT = 75
RSI_OVERSOLD = 25

STATE_FILE = "alerted_today.json"


def load_state(path=STATE_FILE):
    """
    Carga el estado de alertas. Tiene dos partes con vida distinta:
    - "sent": alertas de RSI en zona extrema, se resetea cada dia (UTC).
    - "div_state": ultimo precio del extremo que disparo cada divergencia,
      NO se resetea por fecha, solo cambia cuando aparece un pico/valle
      nuevo de verdad (asi no se repite el aviso mientras sea "la misma"
      divergencia, ni siquiera al dia siguiente).
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            data = {}
    else:
        data = {}

    if data.get("date") != today:
        data["date"] = today
        data["sent"] = []

    data.setdefault("sent", [])
    data.setdefault("div_state", {})
    return data


def save_state(data, path=STATE_FILE):
    with open(path, "w") as f:
        json.dump(data, f)


def divergence_key_changed(state, key, price, tolerance=1e-6):
    """
    Compara el precio del extremo actual con el ultimo guardado para esa
    clave. Devuelve True si es una divergencia "nueva" (primera vez, o el
    extremo ha cambiado de verdad), False si sigue siendo la misma.
    """
    last_price = state["div_state"].get(key)
    if last_price is None:
        return True
    if last_price == 0:
        return price != 0
    return abs(price - last_price) / abs(last_price) > tolerance


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
    """Compara los 2 ultimos picos/valles de precio contra los de RSI para
    detectar divergencia. Devuelve (direccion, precio_del_extremo) o
    (None, None). El precio_del_extremo identifica el pico/valle concreto
    que genera la señal, para poder distinguir una divergencia "nueva" de
    una que sigue activa por el mismo punto de siempre."""
    rsi_series = compute_rsi_series(closes, period=rsi_period)
    if len(rsi_series) < order * 2 + min_distance + 2:
        return None, None

    aligned_closes = closes[-len(rsi_series):]
    peaks, troughs = find_extrema(aligned_closes, order, min_distance)

    bearish = False
    bearish_price = None
    if len(peaks) >= 2:
        i1, i2 = peaks[-2], peaks[-1]
        if aligned_closes[i2] > aligned_closes[i1] and rsi_series[i2] < rsi_series[i1]:
            bearish = True
            bearish_price = aligned_closes[i2]

    bullish = False
    bullish_price = None
    if len(troughs) >= 2:
        i1, i2 = troughs[-2], troughs[-1]
        if aligned_closes[i2] < aligned_closes[i1] and rsi_series[i2] > rsi_series[i1]:
            bullish = True
            bullish_price = aligned_closes[i2]

    if bearish and bullish:
        return None, None  # señales mixtas, no alertamos para evitar ruido
    if bearish:
        return "bajista", bearish_price
    if bullish:
        return "alcista", bullish_price
    return None, None


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


def build_signal_message(color, symbol, description):
    """Formato profesional: SEÑAL DE COMPRA/VENTA + nombre completo + par."""
    label = "SEÑAL DE COMPRA" if color == "🟢" else "SEÑAL DE VENTA"
    name = PAIR_NAMES.get(symbol, symbol)
    return f"{color} {label}\n{name} ({symbol})\n{description}"


def main():
    state = load_state()
    sent = set(state.get("sent", []))

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

        div_daily, div_daily_price = detect_divergence(daily_closes, order=3, min_distance=5)
        div_weekly, div_weekly_price = detect_divergence(weekly_closes, order=2, min_distance=3)

        if label_daily is None and label_weekly is None and div_daily is None and div_weekly is None:
            rd = f"{rsi_daily:.0f}" if rsi_daily is not None else "N/A"
            rw = f"{rsi_weekly:.0f}" if rsi_weekly is not None else "N/A"
            print(f"{symbol}: sin señales (RSI diario {rd}, semanal {rw})")
            continue

        if label_daily:
            key = f"{symbol}:rsi_daily"
            if key not in sent:
                desc = f"RSI diario en {label_daily} ({rsi_daily:.0f})"
                msg = build_signal_message(color_daily, symbol, desc)
                print(msg)
                send_telegram(msg)
                sent.add(key)
            else:
                print(f"{symbol}: RSI diario en {label_daily} pero ya avisado hoy")

        if label_weekly:
            key = f"{symbol}:rsi_weekly"
            if key not in sent:
                desc = f"RSI semanal en {label_weekly} ({rsi_weekly:.0f})"
                msg = build_signal_message(color_weekly, symbol, desc)
                print(msg)
                send_telegram(msg)
                sent.add(key)
            else:
                print(f"{symbol}: RSI semanal en {label_weekly} pero ya avisado hoy")

        div_daily_key = f"{symbol}:div_daily"
        if div_daily:
            if divergence_key_changed(state, div_daily_key, div_daily_price):
                color = "🟢" if div_daily == "alcista" else "🔴"
                desc = f"Divergencia {div_daily} en diario"
                msg = build_signal_message(color, symbol, desc)
                print(msg)
                send_telegram(msg)
                state["div_state"][div_daily_key] = div_daily_price
            else:
                print(f"{symbol}: divergencia {div_daily} diaria, mismo extremo ya avisado")
        elif div_daily_key in state["div_state"]:
            del state["div_state"][div_daily_key]

        div_weekly_key = f"{symbol}:div_weekly"
        if div_weekly:
            if divergence_key_changed(state, div_weekly_key, div_weekly_price):
                color = "🟢" if div_weekly == "alcista" else "🔴"
                desc = f"Divergencia {div_weekly} en semanal"
                msg = build_signal_message(color, symbol, desc)
                print(msg)
                send_telegram(msg)
                state["div_state"][div_weekly_key] = div_weekly_price
            else:
                print(f"{symbol}: divergencia {div_weekly} semanal, mismo extremo ya avisado")
        elif div_weekly_key in state["div_state"]:
            del state["div_state"][div_weekly_key]

    state["sent"] = sorted(sent)
    save_state(state)


if __name__ == "__main__":
    main()
