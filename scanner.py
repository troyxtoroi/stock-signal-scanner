import json
import os
import requests
import yfinance as yf
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator
from datetime import datetime, timedelta
from pywebpush import webpush, WebPushException


def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


def send_notification(topic: str, title: str, message: str, priority: str = "default"):
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={
                "Title": title.encode("utf-8"),
                "Priority": priority,
                "Tags": "chart_with_upwards_trend",
            },
            timeout=10,
        )
    except Exception as e:
        print(f"ntfy 推播失敗: {e}")


def send_web_push(title: str, body: str):
    subscription_json = os.environ.get("PUSH_SUBSCRIPTION", "")
    vapid_private_key = os.environ.get("VAPID_PRIVATE_KEY", "")

    if not subscription_json or not vapid_private_key:
        return

    try:
        subscription_info = json.loads(subscription_json)
        webpush(
            subscription_info=subscription_info,
            data=json.dumps({"title": title, "body": body}),
            vapid_private_key=vapid_private_key,
            vapid_claims={"sub": "mailto:coolzoro58@gmail.com"},
        )
        print(f"  Chrome 推播成功：{title}")
    except WebPushException as e:
        print(f"  Chrome 推播失敗: {e}")


def fetch_stock_data(ticker: str, days: int = 60) -> pd.DataFrame:
    end = datetime.today()
    start = end - timedelta(days=days)
    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    return df


def check_golden_cross(df: pd.DataFrame) -> bool:
    """短均線（5日）由下往上穿越長均線（20日）"""
    if len(df) < 21:
        return False
    close = df["Close"].squeeze()
    ma5 = SMAIndicator(close, window=5).sma_indicator()
    ma20 = SMAIndicator(close, window=20).sma_indicator()
    return (ma5.iloc[-2] < ma20.iloc[-2]) and (ma5.iloc[-1] > ma20.iloc[-1])


def check_volume_spike(df: pd.DataFrame, ratio: float) -> bool:
    """今日成交量超過 20 日平均成交量的 N 倍"""
    if len(df) < 21:
        return False
    avg_volume = df["Volume"].iloc[-21:-1].mean()
    today_volume = df["Volume"].iloc[-1]
    return float(today_volume) > avg_volume * ratio


def check_rsi_oversold(df: pd.DataFrame, level: float) -> bool:
    """RSI 低於超賣線後回升（前一天低於門檻、今天回升）"""
    if len(df) < 15:
        return False
    close = df["Close"].squeeze()
    rsi = RSIIndicator(close, window=14).rsi()
    return (rsi.iloc[-2] < level) and (rsi.iloc[-1] >= level)


def get_dividend_yield(ticker: str) -> float:
    """取得殖利率（百分比）"""
    try:
        info = yf.Ticker(ticker).info
        return float(info.get("dividendYield", 0) or 0) * 100
    except Exception:
        return 0.0


def scan_stock(ticker: str, cfg: dict) -> list[str]:
    signals_found = []
    thresholds = cfg["thresholds"]
    enabled = cfg["signals"]

    df = fetch_stock_data(ticker)
    if df.empty or len(df) < 5:
        return signals_found

    if enabled.get("golden_cross") and check_golden_cross(df):
        signals_found.append("均線黃金交叉（短線買入信號）")

    if enabled.get("volume_spike") and check_volume_spike(df, thresholds["volume_spike_ratio"]):
        signals_found.append(f"成交量異常放大（超過均量 {thresholds['volume_spike_ratio']} 倍）")

    if enabled.get("rsi_oversold") and check_rsi_oversold(df, thresholds["rsi_oversold_level"]):
        signals_found.append(f"RSI 超賣反彈（剛從 {thresholds['rsi_oversold_level']} 以下回升）")

    if enabled.get("high_dividend"):
        dy = get_dividend_yield(ticker)
        if dy >= thresholds["min_dividend_yield"]:
            signals_found.append(f"高殖利率 {dy:.1f}%（超過 {thresholds['min_dividend_yield']}% 門檻）")

    return signals_found


def main():
    cfg = load_config()
    topic = cfg["ntfy_topic"]
    watchlist = cfg["watchlist"]
    today = datetime.today().strftime("%Y/%m/%d")

    print(f"開始掃描 {len(watchlist)} 支股票... ({today})")
    opportunities = []

    for ticker in watchlist:
        print(f"  掃描中：{ticker}")
        signals = scan_stock(ticker, cfg)
        if signals:
            opportunities.append((ticker, signals))

    if opportunities:
        for ticker, signals in opportunities:
            signal_text = "\n".join(f"• {s}" for s in signals)
            title = f"📈 {ticker} 出現買入信號！"
            message = f"日期：{today}\n股票：{ticker}\n\n{signal_text}"
            send_notification(topic=topic, title=title, message=message, priority="high")
            send_web_push(title=title, body=f"{ticker}：{signal_text}")
            print(f"  ✓ 已推播：{ticker} - {signals}")
    else:
        title = f"📊 每日掃描完成 ({today})"
        message = f"掃描了 {len(watchlist)} 支股票，今日無明顯信號。"
        send_notification(topic=topic, title=title, message=message)
        send_web_push(title=title, body=message)
        print("今日無明顯信號。")

    print("掃描完成！")


if __name__ == "__main__":
    main()
