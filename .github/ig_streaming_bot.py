import requests
import time
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from lightstreamer_client import LightstreamerClient, Subscription

# ==== CONFIGURE YOUR CREDENTIALS HERE ====
IG_API_KEY = "f1dec4c2a4563648be395ba138423263be04a01b"
IG_USERNAME = "NAYAME69312119"
IG_PASSWORD = "Melwin@4203"
EPIC = "CS.D.EURUSD.DUB.IP"
SIZE = 0.5  # lot size

BASE_URL = "https://api.ig.com/gateway/deal"

TELEGRAM_BOTS = [
    {"token": "8018228539:AAEqqGfhBGnOeocAKmMU68Pl6OEecDHfPeI", "chat_id": "5661218799"},
    {"token": "8145443663:AAFCZT20jBJwy5qaSOtFc4dkOS5yJu14Kas", "chat_id": "844160087"}
]

HEADERS = {
    "X-IG-API-KEY": IG_API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json"
}

# Candle storage for aggregation
candles_15m = []
candles_1h = []

def send_telegram(message):
    for bot in TELEGRAM_BOTS:
        url = f"https://api.telegram.org/bot{bot['token']}/sendMessage"
        data = {"chat_id": bot["chat_id"], "text": message}
        try:
            requests.post(url, data=data)
        except Exception as e:
            print("Telegram send error:", e)

def login():
    url = f"{BASE_URL}/session"
    payload = {
        "identifier": IG_USERNAME,
        "password": IG_PASSWORD,
        "encryptedPassword": False
    }
    response = requests.post(url, headers=HEADERS, json=payload)
    response.raise_for_status()
    data = response.json()
    cst = response.headers.get("CST")
    xst = response.headers.get("X-SECURITY-TOKEN")
    ls_endpoint = data.get("lightstreamerEndpoint")
    account_id = data.get("currentAccountId")
    print("Login successful.")
    return cst, xst, ls_endpoint, account_id

def build_candles(ticks, timeframe_minutes=15):
    """
    ticks: list of dicts {"timestamp":datetime, "bid":float, "offer":float}
    Return candles list with dicts {open, high, low, close, volume, start_time}
    """
    if not ticks:
        return []

    # Sort ticks by timestamp
    ticks = sorted(ticks, key=lambda x: x["timestamp"])

    candles = []
    bucket = []
    bucket_start = ticks[0]["timestamp"].replace(second=0, microsecond=0)
    bucket_start = bucket_start - timedelta(minutes=bucket_start.minute % timeframe_minutes)

    for tick in ticks:
        ts = tick["timestamp"]
        if ts >= bucket_start + timedelta(minutes=timeframe_minutes):
            # Aggregate bucket into candle
            df = pd.DataFrame(bucket)
            candle = {
                "start_time": bucket_start,
                "open": df["bid"].iloc[0],
                "high": df["bid"].max(),
                "low": df["bid"].min(),
                "close": df["bid"].iloc[-1],
                "volume": len(bucket)
            }
            candles.append(candle)
            bucket_start = bucket_start + timedelta(minutes=timeframe_minutes)
            bucket = []

        bucket.append(tick)

    # Last partial bucket candle
    if bucket:
        df = pd.DataFrame(bucket)
        candle = {
            "start_time": bucket_start,
            "open": df["bid"].iloc[0],
            "high": df["bid"].max(),
            "low": df["bid"].min(),
            "close": df["bid"].iloc[-1],
            "volume": len(bucket)
        }
        candles.append(candle)

    return candles

def calculate_vwap(candles):
    df = pd.DataFrame(candles)
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    vwap = (typical_price * df["volume"]).cumsum() / df["volume"].cumsum()
    return vwap.iloc[-1]

def calculate_atr(candles, period=14):
    df = pd.DataFrame(candles)
    df["high_low"] = df["high"] - df["low"]
    df["high_close_prev"] = np.abs(df["high"] - df["close"].shift(1))
    df["low_close_prev"] = np.abs(df["low"] - df["close"].shift(1))
    df["true_range"] = df[["high_low", "high_close_prev", "low_close_prev"]].max(axis=1)
    atr = df["true_range"].rolling(window=period).mean()
    return atr.iloc[-1]

def check_conditions(candles_15m, candles_1h):
    # Simplified example: bullish if close > vwap, else bearish
    vwap_15m = calculate_vwap(candles_15m)
    vwap_1h = calculate_vwap(candles_1h)
    close_15m = candles_15m[-1]["close"]
    close_1h = candles_1h[-1]["close"]

    conditions_met = 0
    bias = None

    if close_15m > vwap_15m and close_1h > vwap_1h:
        bias = "bull"
        conditions_met += 6  # Replace with actual condition counts
    else:
        bias = "bear"
        conditions_met += 6  # Replace with actual condition counts

    return conditions_met, bias

def place_order(direction, cst, xst, account_id, atr):
    stop_distance = round(atr / 4, 5)  # 1:4 RR stop loss
    limit_distance = round(atr, 5)

    url = f"{BASE_URL}/positions/otc"
    headers = HEADERS.copy()
    headers["CST"] = cst
    headers["X-SECURITY-TOKEN"] = xst

    payload = {
        "epic": EPIC,
        "expiry": "-",
        "direction": direction,
        "size": SIZE,
        "orderType": "MARKET",
        "currencyCode": "USD",
        "forceOpen": True,
        "guaranteedStop": False,
        "stopDistance": stop_distance,
        "limitDistance": limit_distance,
        "accountId": account_id
    }

    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()
    print(f"{direction} order placed:", json.dumps(data, indent=2))
    send_telegram(f"{direction} order placed with size {SIZE}. SL distance: {stop_distance}, TP distance: {limit_distance}")

def run_bot():
    cst, xst, ls_endpoint, account_id = login()

    client = LightstreamerClient(
        server=ls_endpoint,
        user=account_id,
        password=f"CST-{cst}|XST-{xst}"
    )

    ticks = []

    subscription = Subscription(
        mode="MERGE",
        items=[f"MARKET:{EPIC}"],
        fields=["BID", "OFFER", "TIMESTAMP"]
    )

    def on_item_update(update):
        bid = update.get_value("BID")
        timestamp_str = update.get_value("TIMESTAMP")
        timestamp = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        ticks.append({"timestamp": timestamp, "bid": float(bid)})

        # Aggregate and trade every 15 minutes (simple example)
        if len(ticks) > 500:  # to limit memory
            ticks.pop(0)

        # Build candles every 15 min and 1 hour
        global candles_15m, candles_1h
        candles_15m = build_candles(ticks, 15)
        candles_1h = build_candles(ticks, 60)

        # Check conditions only at candle close (simplified check)
        now = datetime.utcnow()
        if now.minute % 15 == 0 and now.second < 5:
            try:
                conditions_met, bias = check_conditions(candles_15m, candles_1h)
                print(f"Conditions met: {conditions_met}, Bias: {bias}")
                if conditions_met >= 6:
                    place_order(bias.upper(), cst, xst, account_id, calculate_atr(candles_15m))
            except Exception as e:
                print("Error in trading logic:", e)

    subscription.add_listener({"on_item_update": on_item_update})
    client.subscribe(subscription)
    client.connect()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Disconnecting...")
        client.unsubscribe(subscription)
        client.disconnect()

if __name__ == "__main__":
    run_bot()
