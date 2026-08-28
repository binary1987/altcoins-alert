#!/usr/bin/env python3
# dominance_signals.py
import os
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone

GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"

HISTORY_FILE = "dominance_history.json"
STATE_FILE = "dominance_alerted.json"

RSI_OVERBOUGHT = 75
RSI_OVERSOLD = 25

# Umbrales de fiabilidad: minimo de puntos antes de empezar a avisar de
# cada tipo de señal. No son los minimos matematicos del calculo (esos son
# mas bajos), son un margen extra para que el dato tenga sentido real.
MIN_DAILY_FOR_RSI = 30
MIN_DAILY_FOR_DIVERGENCE = 90
MIN_WEEKLY_FOR_RSI = 20
MIN_WEEKLY_FOR_DIVERGENCE = 30


def cg_headers():
    api_key = os.environ.get("COINGECKO_API_KEY")
    return {"x-cg-demo-api-key": api_key} if api_key else {}


def get_usdt_dominance(retries=3, retry_delay=10):
    """
    Calcula la dominancia actual de USDT (market cap USDT / market cap
    total del mercado cripto) * 100. Reintenta ante fallos de red.
    """
    import time as _time

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            req_global = urllib.request.Request(GLOBAL_URL, headers=cg_headers())
            with urllib.request.urlopen(req_global, timeout=15) as r:
                global_data = json.loads(r.read().decode())
            total_market_cap = global_data["data"]["total_market_cap"]["usd"]

            params = urllib.parse.urlencode({"vs_currency": "usd", "ids": "tether"})
            req_markets = urllib.request.Request(f"{MARKETS_URL}?{params}", headers=cg_headers())
            with urllib.request.urlopen(req_markets, timeout=15) as r:
                markets_data = json.loads(r.read().decode())
            usdt_market_cap = markets_data[0]["market_cap"]

            return (usdt_market_cap / total_market_cap) * 100
        except Exception as e:
            last_error = e
            print(f"Aviso: fallo al calcular dominancia USDT (intento {attempt}/{retries}): {e}")
            if attempt < retries:
                _time.sleep(retry_delay)

    raise last_error


def read_history(path=HISTORY_FILE):
    """Devuelve una lista de (fecha, valor) en orden cronologico."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        data = json.load(f)
    return [(entry["date"], entry["value"]) for entry in data]


def append_history(value, path=HISTORY_FILE):
    """Guarda el dato de hoy si no se habia guardado ya."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history = read_history(path)
    existing_dates = {h[0] for h in history}
    if today_str in existing_dates:
        return False

    history.append((today_str, value))
    data = [{"date": d, "value": v} for d, v in history]
    with open(path, "w") as f:
        json.dump(data, f, indent=0)
    return True


def weekly_from_daily(history):
    """
    Agrupa la serie diaria en semanal, quedandose con el ultimo valor
    disponible de cada semana ISO (mismo criterio que group_last en los
    otros bots).
    """
    groups = {}
    order = []
    for date_str, value in history:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        key = dt.isocalendar()[:2]  # (año ISO, semana ISO)
        if key not in groups:
            order.append(key)
        groups[key] = value
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
    """Misma logica que en los otros bots: compara los 2 ultimos
    picos/valles de precio contra los de RSI."""
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
        return None, None
    if bearish:
        return "bajista", bearish_price
    if bullish:
        return "alcista", bullish_price
    return None, None


def zone_info(rsi):
    if rsi is None:
        return None, None
    if rsi >= RSI_OVERBOUGHT:
        return "sobrecompra", "🔴"
    if rsi <= RSI_OVERSOLD:
        return "sobreventa", "🟢"
    return None, None


def load_state(path=STATE_FILE):
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
    last_price = state["div_state"].get(key)
    if last_price is None:
        return True
    if last_price == 0:
        return price != 0
    return abs(price - last_price) / abs(last_price) > tolerance


def send_telegram(msg):
    token = os.environ.get("TELEGRAM_BOT_TOKEN_ALT")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": msg}).encode()
    urllib.request.urlopen(url, data=data, timeout=10)


def build_signal_message(color, description):
    label = "SEÑAL DE COMPRA" if color == "🟢" else "SEÑAL DE VENTA"
    return f"{color} {label}\nUSDT Dominance (USDT.D)\n{description}"


def main():
    dominance = get_usdt_dominance()
    print(f"Dominancia USDT hoy: {dominance:.2f}%")
    is_new = append_history(dominance)
    print("Guardado nuevo punto de hoy" if is_new else "Ya existia un punto para hoy, no se duplica")

    history = read_history()
    daily_values = [v for _, v in history]
    weekly_values = weekly_from_daily(history)

    print(f"Histórico acumulado: {len(daily_values)} días / {len(weekly_values)} semanas")

    state = load_state()
    sent = set(state.get("sent", []))

    # --- RSI diario ---
    if len(daily_values) >= MIN_DAILY_FOR_RSI:
        rsi_daily = compute_rsi(daily_values)
        label, color = zone_info(rsi_daily)
        if label:
            key = "usdtd:rsi_daily"
            if key not in sent:
                desc = f"RSI diario en {label} ({rsi_daily:.0f})"
                msg = build_signal_message(color, desc)
                print(msg)
                send_telegram(msg)
                sent.add(key)
            else:
                print(f"RSI diario en {label} pero ya avisado hoy")
        else:
            print(f"RSI diario: {rsi_daily:.0f} (sin señal)")
    else:
        print(f"RSI diario: acumulando histórico ({len(daily_values)}/{MIN_DAILY_FOR_RSI} días)")

    # --- RSI semanal ---
    if len(weekly_values) >= MIN_WEEKLY_FOR_RSI:
        rsi_weekly = compute_rsi(weekly_values)
        label, color = zone_info(rsi_weekly)
        if label:
            key = "usdtd:rsi_weekly"
            if key not in sent:
                desc = f"RSI semanal en {label} ({rsi_weekly:.0f})"
                msg = build_signal_message(color, desc)
                print(msg)
                send_telegram(msg)
                sent.add(key)
            else:
                print(f"RSI semanal en {label} pero ya avisado hoy")
        else:
            print(f"RSI semanal: {rsi_weekly:.0f} (sin señal)")
    else:
        print(f"RSI semanal: acumulando histórico ({len(weekly_values)}/{MIN_WEEKLY_FOR_RSI} semanas)")

    # --- Divergencia diaria ---
    if len(daily_values) >= MIN_DAILY_FOR_DIVERGENCE:
        div_daily, div_daily_price = detect_divergence(daily_values, order=3, min_distance=5)
        key = "usdtd:div_daily"
        if div_daily:
            if divergence_key_changed(state, key, div_daily_price):
                color = "🟢" if div_daily == "alcista" else "🔴"
                desc = f"Divergencia {div_daily} en diario"
                msg = build_signal_message(color, desc)
                print(msg)
                send_telegram(msg)
                state["div_state"][key] = div_daily_price
            else:
                print(f"Divergencia {div_daily} diaria, mismo extremo ya avisado")
        elif key in state["div_state"]:
            del state["div_state"][key]
    else:
        print(f"Divergencia diaria: acumulando histórico ({len(daily_values)}/{MIN_DAILY_FOR_DIVERGENCE} días)")

    # --- Divergencia semanal ---
    if len(weekly_values) >= MIN_WEEKLY_FOR_DIVERGENCE:
        div_weekly, div_weekly_price = detect_divergence(weekly_values, order=2, min_distance=3)
        key = "usdtd:div_weekly"
        if div_weekly:
            if divergence_key_changed(state, key, div_weekly_price):
                color = "🟢" if div_weekly == "alcista" else "🔴"
                desc = f"Divergencia {div_weekly} en semanal"
                msg = build_signal_message(color, desc)
                print(msg)
                send_telegram(msg)
                state["div_state"][key] = div_weekly_price
            else:
                print(f"Divergencia {div_weekly} semanal, mismo extremo ya avisado")
        elif key in state["div_state"]:
            del state["div_state"][key]
    else:
        print(f"Divergencia semanal: acumulando histórico ({len(weekly_values)}/{MIN_WEEKLY_FOR_DIVERGENCE} semanas)")

    state["sent"] = sorted(sent)
    save_state(state)


if __name__ == "__main__":
    main()
