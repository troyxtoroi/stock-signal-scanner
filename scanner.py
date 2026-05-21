import json
import os
import requests
import yfinance as yf
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator, MACD
from ta.volatility import BollingerBands
from datetime import datetime, timedelta
from pywebpush import webpush, WebPushException


def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


# ── 推播 ──────────────────────────────────────────────────────────────


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
        print(f"  ntfy 推播失敗: {e}")


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


def notify_all(topic: str, title: str, message: str, priority: str = "default"):
    send_notification(topic=topic, title=title, message=message, priority=priority)
    send_web_push(title=title, body=message)


# ── 資料取得 ───────────────────────────────────────────────────────────


def fetch_stock_data(ticker: str, days: int = 90) -> pd.DataFrame:
    end = datetime.today()
    start = end - timedelta(days=days)
    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    return df


def get_dividend_yield(ticker: str) -> float:
    try:
        info = yf.Ticker(ticker).info
        return float(info.get("dividendYield", 0) or 0) * 100
    except Exception:
        return 0.0


def get_etf_nav(ticker_no_tw: str) -> float:
    """從 FinMind 取得 ETF 最新淨值（NAV）"""
    try:
        start = (datetime.today() - timedelta(days=10)).strftime("%Y-%m-%d")
        resp = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params={
                "dataset": "TaiwanETFNetAssetValue",
                "data_id": ticker_no_tw,
                "start_date": start,
            },
            timeout=15,
        )
        data = resp.json().get("data", [])
        if not data:
            return 0.0
        return float(data[-1].get("net_asset_value", 0))
    except Exception:
        return 0.0


# ── 信號偵測 ───────────────────────────────────────────────────────────


def check_golden_cross(df: pd.DataFrame) -> bool:
    if len(df) < 21:
        return False
    close = df["Close"].squeeze()
    ma5 = SMAIndicator(close, window=5).sma_indicator()
    ma20 = SMAIndicator(close, window=20).sma_indicator()
    return bool((ma5.iloc[-2] < ma20.iloc[-2]) and (ma5.iloc[-1] > ma20.iloc[-1]))


def check_macd_crossover(df: pd.DataFrame) -> bool:
    """MACD 線由下往上穿越訊號線（黃金交叉）"""
    if len(df) < 35:
        return False
    close = df["Close"].squeeze()
    macd = MACD(close)
    macd_line = macd.macd()
    signal_line = macd.macd_signal()
    return bool(
        (macd_line.iloc[-2] < signal_line.iloc[-2])
        and (macd_line.iloc[-1] > signal_line.iloc[-1])
    )


def check_bollinger_bounce(df: pd.DataFrame, pband_threshold: float) -> bool:
    """價格從布林通道下緣反彈（前日觸碰下緣，今日回升）"""
    if len(df) < 22:
        return False
    close = df["Close"].squeeze()
    bb = BollingerBands(close, window=20, window_dev=2)
    pband = bb.bollinger_pband()
    return bool((pband.iloc[-2] <= pband_threshold) and (pband.iloc[-1] > pband_threshold))


def check_volume_spike(df: pd.DataFrame, ratio: float) -> bool:
    if len(df) < 21:
        return False
    avg_volume = df["Volume"].iloc[-21:-1].mean()
    today_volume = float(df["Volume"].iloc[-1])
    return today_volume > avg_volume * ratio


def check_rsi_oversold(df: pd.DataFrame, level: float) -> bool:
    if len(df) < 15:
        return False
    close = df["Close"].squeeze()
    rsi = RSIIndicator(close, window=14).rsi()
    return bool((rsi.iloc[-2] < level) and (rsi.iloc[-1] >= level))


# ── 掃描邏輯 ───────────────────────────────────────────────────────────


def scan_stock(ticker: str, cfg: dict) -> list[str]:
    signals_found = []
    thresholds = cfg["thresholds"]
    enabled = cfg["signals"]

    df = fetch_stock_data(ticker)
    if df.empty or len(df) < 5:
        return signals_found

    if enabled.get("golden_cross") and check_golden_cross(df):
        signals_found.append("均線黃金交叉（MA5 穿越 MA20）")

    if enabled.get("macd_crossover") and check_macd_crossover(df):
        signals_found.append("MACD 黃金交叉（MACD 線穿越訊號線）")

    if enabled.get("bollinger_bounce") and check_bollinger_bounce(
        df, thresholds["bollinger_pband_entry"]
    ):
        signals_found.append("布林通道下緣反彈（超賣後回升）")

    if enabled.get("volume_spike") and check_volume_spike(df, thresholds["volume_spike_ratio"]):
        signals_found.append(f"成交量異常放大（超過均量 {thresholds['volume_spike_ratio']} 倍）")

    if enabled.get("rsi_oversold") and check_rsi_oversold(df, thresholds["rsi_oversold_level"]):
        signals_found.append(f"RSI 超賣反彈（RSI 從 {thresholds['rsi_oversold_level']} 以下回升）")

    if enabled.get("high_dividend"):
        dy = get_dividend_yield(ticker)
        if dy >= thresholds["min_dividend_yield"]:
            signals_found.append(f"高殖利率 {dy:.1f}%（門檻 {thresholds['min_dividend_yield']}%）")

    return signals_found


def scan_etf_arbitrage(cfg: dict) -> list[tuple[str, str]]:
    """偵測 ETF 折價機會（市價低於淨值超過門檻）"""
    opportunities = []
    threshold = cfg["thresholds"]["etf_discount_threshold"]
    etf_list = cfg.get("etf_watchlist", [])

    for ticker in etf_list:
        ticker_id = ticker.replace(".TW", "")
        df = fetch_stock_data(ticker, days=5)
        if df.empty:
            continue

        market_price = float(df["Close"].iloc[-1])
        nav = get_etf_nav(ticker_id)
        if nav <= 0:
            continue

        discount_pct = (market_price - nav) / nav * 100

        if discount_pct <= threshold:
            msg = (
                f"市價 {market_price:.2f} vs 淨值 {nav:.2f}，"
                f"折價 {discount_pct:.2f}%（門檻 {threshold}%）"
            )
            opportunities.append((ticker, msg))
            print(f"  套利機會：{ticker} {msg}")

    return opportunities


# ── 主程式 ────────────────────────────────────────────────────────────


def main():
    cfg = load_config()
    topic = cfg["ntfy_topic"]
    watchlist = cfg["watchlist"]
    today = datetime.today().strftime("%Y/%m/%d")

    print(f"\n{'='*50}")
    print(f"台股信號掃描 {today}")
    print(f"{'='*50}")

    # ── 個股信號掃描 ──
    print(f"\n[個股掃描] 共 {len(watchlist)} 支")
    stock_opportunities = []
    for ticker in watchlist:
        print(f"  掃描：{ticker}", end=" ", flush=True)
        signals = scan_stock(ticker, cfg)
        if signals:
            stock_opportunities.append((ticker, signals))
            print(f"→ {len(signals)} 個信號")
        else:
            print("→ 無信號")

    # ── ETF 套利掃描 ──
    etf_list = cfg.get("etf_watchlist", [])
    arbitrage_opportunities = []
    if cfg["signals"].get("etf_arbitrage") and etf_list:
        print(f"\n[ETF 套利掃描] 共 {len(etf_list)} 支 ETF")
        arbitrage_opportunities = scan_etf_arbitrage(cfg)

    # ── 推播結果 ──
    print(f"\n[推播結果]")

    if stock_opportunities:
        for ticker, signals in stock_opportunities:
            signal_text = "\n".join(f"• {s}" for s in signals)
            title = f"📈 {ticker} 出現 {len(signals)} 個買入信號！"
            message = f"日期：{today}\n\n{signal_text}"
            notify_all(topic=topic, title=title, message=message, priority="high")
            print(f"  ✓ 推播：{ticker}")
    else:
        print("  今日個股無明顯信號")

    if arbitrage_opportunities:
        for ticker, detail in arbitrage_opportunities:
            title = f"💰 {ticker} ETF 折價套利機會！"
            message = f"日期：{today}\n\n{detail}"
            notify_all(topic=topic, title=title, message=message, priority="high")
            print(f"  ✓ 推播：{ticker} 套利")
    else:
        print("  今日 ETF 無折價套利機會")

    if not stock_opportunities and not arbitrage_opportunities:
        title = f"📊 每日掃描完成 ({today})"
        message = f"掃描了 {len(watchlist)} 支股票、{len(etf_list)} 支 ETF，今日無明顯信號。"
        notify_all(topic=topic, title=title, message=message)

    print(f"\n掃描完成！")


if __name__ == "__main__":
    main()
