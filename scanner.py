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


def load_portfolio():
    try:
        with open("portfolio.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"holdings": [], "stop_loss_pct": -8.0, "take_profit_pct": 20.0}


# ═══════════════════════════════════════════════════════════════
# 推播
# ═══════════════════════════════════════════════════════════════

def send_ntfy(topic: str, title: str, message: str, priority: str = "default"):
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
        print(f"    ntfy 失敗: {e}")


def send_web_push(title: str, body: str):
    sub_json = os.environ.get("PUSH_SUBSCRIPTION", "").strip()
    vapid_key = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    if not sub_json or not vapid_key:
        print("    Chrome 推播跳過：缺少 PUSH_SUBSCRIPTION 或 VAPID_PRIVATE_KEY")
        return
    try:
        webpush(
            subscription_info=json.loads(sub_json),
            data=json.dumps({"title": title, "body": body}),
            vapid_private_key=vapid_key,
            vapid_claims={"sub": "mailto:coolzoro58@gmail.com"},
        )
        print(f"    Chrome 推播成功")
    except Exception as e:
        print(f"    Chrome 推播失敗: {type(e).__name__}: {e}")


def notify(topic: str, title: str, message: str, priority: str = "default"):
    print(f"  📣 {title}")
    send_ntfy(topic=topic, title=title, message=message, priority=priority)
    send_web_push(title=title, body=message)


# ═══════════════════════════════════════════════════════════════
# 資料取得
# ═══════════════════════════════════════════════════════════════

def fetch_ohlcv(ticker: str, days: int = 90) -> pd.DataFrame:
    end = datetime.today()
    start = end - timedelta(days=days)
    return yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)


def get_current_price(ticker: str) -> float:
    try:
        return float(yf.Ticker(ticker).fast_info["lastPrice"])
    except Exception:
        df = fetch_ohlcv(ticker, days=5)
        return float(df["Close"].iloc[-1]) if not df.empty else 0.0


def get_dividend_yield(ticker: str) -> float:
    try:
        info = yf.Ticker(ticker).info
        return float(info.get("dividendYield", 0) or 0) * 100
    except Exception:
        return 0.0


def get_usd_twd() -> float:
    try:
        return float(yf.Ticker("USDTWD=X").fast_info["lastPrice"])
    except Exception:
        return 32.0


def get_etf_nav(ticker_id: str) -> float:
    try:
        start = (datetime.today() - timedelta(days=10)).strftime("%Y-%m-%d")
        resp = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params={"dataset": "TaiwanETFNetAssetValue", "data_id": ticker_id, "start_date": start},
            timeout=15,
        )
        data = resp.json().get("data", [])
        return float(data[-1]["net_asset_value"]) if data else 0.0
    except Exception:
        return 0.0


def get_institutional_net(ticker_id: str) -> dict:
    """取得外資、投信昨日買賣超（單位：張）"""
    try:
        start = (datetime.today() - timedelta(days=5)).strftime("%Y-%m-%d")
        resp = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params={
                "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
                "data_id": ticker_id,
                "start_date": start,
            },
            timeout=15,
        )
        data = resp.json().get("data", [])
        if not data:
            return {}
        last = data[-1]
        return {
            "foreign_net": float(last.get("Foreign_Investor_Buy_Sell", 0)),
            "trust_net":   float(last.get("Investment_Trust_Buy_Sell", 0)),
            "date":        last.get("date", ""),
        }
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════
# 技術面信號
# ═══════════════════════════════════════════════════════════════

def check_golden_cross(df: pd.DataFrame) -> bool:
    if len(df) < 21:
        return False
    c = df["Close"].squeeze()
    ma5  = SMAIndicator(c, window=5).sma_indicator()
    ma20 = SMAIndicator(c, window=20).sma_indicator()
    return bool((ma5.iloc[-2] < ma20.iloc[-2]) and (ma5.iloc[-1] > ma20.iloc[-1]))


def check_macd_crossover(df: pd.DataFrame) -> bool:
    if len(df) < 35:
        return False
    c = df["Close"].squeeze()
    macd   = MACD(c).macd()
    signal = MACD(c).macd_signal()
    return bool((macd.iloc[-2] < signal.iloc[-2]) and (macd.iloc[-1] > signal.iloc[-1]))


def check_bollinger_bounce(df: pd.DataFrame, threshold: float) -> bool:
    if len(df) < 22:
        return False
    c = df["Close"].squeeze()
    pband = BollingerBands(c, window=20, window_dev=2).bollinger_pband()
    return bool((pband.iloc[-2] <= threshold) and (pband.iloc[-1] > threshold))


def check_volume_spike(df: pd.DataFrame, ratio: float) -> bool:
    if len(df) < 21:
        return False
    avg = df["Volume"].iloc[-21:-1].mean()
    return float(df["Volume"].iloc[-1]) > avg * ratio


def check_rsi_oversold(df: pd.DataFrame, level: float) -> bool:
    if len(df) < 15:
        return False
    rsi = RSIIndicator(df["Close"].squeeze(), window=14).rsi()
    return bool((rsi.iloc[-2] < level) and (rsi.iloc[-1] >= level))


# ═══════════════════════════════════════════════════════════════
# 個股掃描（技術面 + 基本面）
# ═══════════════════════════════════════════════════════════════

def scan_stock(ticker: str, cfg: dict) -> list[str]:
    signals = []
    t = cfg["thresholds"]
    e = cfg["signals"]

    df = fetch_ohlcv(ticker)
    if df.empty or len(df) < 5:
        return signals

    if e.get("golden_cross")     and check_golden_cross(df):
        signals.append("均線黃金交叉（MA5 穿越 MA20）")
    if e.get("macd_crossover")   and check_macd_crossover(df):
        signals.append("MACD 黃金交叉（動能轉強）")
    if e.get("bollinger_bounce") and check_bollinger_bounce(df, t["bollinger_pband_entry"]):
        signals.append("布林通道下緣反彈（超賣後回升）")
    if e.get("volume_spike")     and check_volume_spike(df, t["volume_spike_ratio"]):
        signals.append(f"成交量爆量（超過均量 {t['volume_spike_ratio']} 倍）")
    if e.get("rsi_oversold")     and check_rsi_oversold(df, t["rsi_oversold_level"]):
        signals.append(f"RSI 超賣反彈（從 {t['rsi_oversold_level']} 以下回升）")
    if e.get("high_dividend"):
        dy = get_dividend_yield(ticker)
        if dy >= t["min_dividend_yield"]:
            signals.append(f"高殖利率 {dy:.1f}%（門檻 {t['min_dividend_yield']}%）")

    return signals


# ═══════════════════════════════════════════════════════════════
# 模組一：重大公告 / 除息除權提醒
# ═══════════════════════════════════════════════════════════════

def scan_announcements(watchlist: list[str], days_ahead: int) -> list[tuple[str, str]]:
    results = []
    today = datetime.today().date()
    deadline = today + timedelta(days=days_ahead)

    for ticker in watchlist:
        try:
            t = yf.Ticker(ticker)
            cal = t.calendar
            if cal is None or cal.empty:
                continue

            if "Ex-Dividend Date" in cal.index:
                ex_div = pd.Timestamp(cal.loc["Ex-Dividend Date"].iloc[0]).date()
                if today <= ex_div <= deadline:
                    results.append((ticker, f"除息日即將到來：{ex_div}（{(ex_div - today).days} 天後）"))

            if "Earnings Date" in cal.index:
                earn = pd.Timestamp(cal.loc["Earnings Date"].iloc[0]).date()
                if today <= earn <= deadline:
                    results.append((ticker, f"法說會 / 財報日：{earn}（{(earn - today).days} 天後）"))
        except Exception:
            continue

    return results


# ═══════════════════════════════════════════════════════════════
# 模組二：停損 / 停利提醒
# ═══════════════════════════════════════════════════════════════

def scan_portfolio_alerts(portfolio: dict) -> list[tuple[str, str, str]]:
    results = []
    stop_loss   = portfolio.get("stop_loss_pct", -8.0)
    take_profit = portfolio.get("take_profit_pct", 20.0)

    for h in portfolio.get("holdings", []):
        ticker     = h["ticker"]
        name       = h.get("name", ticker)
        buy_price  = h["buy_price"]
        price = get_current_price(ticker)
        if price <= 0:
            continue

        pct = (price - buy_price) / buy_price * 100

        if pct <= stop_loss:
            results.append((ticker, "🔴 停損警告", f"{name} 虧損 {pct:.1f}%（買入 {buy_price}，現價 {price:.1f}）"))
        elif pct >= take_profit:
            results.append((ticker, "🟢 停利提醒", f"{name} 獲利 {pct:.1f}%（買入 {buy_price}，現價 {price:.1f}）"))

    return results


# ═══════════════════════════════════════════════════════════════
# 模組三：籌碼面（外資 + 投信）
# ═══════════════════════════════════════════════════════════════

def scan_institutional(watchlist: list[str], thresholds: dict) -> list[tuple[str, str]]:
    results = []
    f_min = thresholds.get("foreign_net_buy_million", 500) * 1000   # 換算成張（百萬÷千）→ 千張
    tr_min = thresholds.get("trust_net_buy_million", 200) * 1000

    # FinMind 單位是「股」，1張=1000股，所以門檻要×1000
    f_min_shares  = thresholds.get("foreign_net_buy_million", 500)   * 1_000_000
    tr_min_shares = thresholds.get("trust_net_buy_million",   200)   * 1_000_000

    for ticker in watchlist:
        ticker_id = ticker.replace(".TW", "")
        data = get_institutional_net(ticker_id)
        if not data:
            continue

        msgs = []
        foreign_net = data.get("foreign_net", 0)
        trust_net   = data.get("trust_net", 0)

        if foreign_net >= f_min_shares:
            msgs.append(f"外資買超 {foreign_net/1_000_000:.0f} 百萬股")
        if trust_net >= tr_min_shares:
            msgs.append(f"投信買超 {trust_net/1_000_000:.0f} 百萬股")

        if msgs:
            results.append((ticker, "、".join(msgs)))

    return results


# ═══════════════════════════════════════════════════════════════
# 模組四：ETF 折價 + ADR 套利
# ═══════════════════════════════════════════════════════════════

def scan_etf_arbitrage(cfg: dict) -> list[tuple[str, str]]:
    results = []
    threshold = cfg["thresholds"]["etf_discount_threshold"]
    for ticker in cfg.get("etf_watchlist", []):
        ticker_id = ticker.replace(".TW", "")
        df = fetch_ohlcv(ticker, days=5)
        if df.empty:
            continue
        price = float(df["Close"].iloc[-1])
        nav   = get_etf_nav(ticker_id)
        if nav <= 0:
            continue
        discount = (price - nav) / nav * 100
        if discount <= threshold:
            results.append((ticker, f"市價 {price:.2f} vs 淨值 {nav:.2f}，折價 {discount:.2f}%"))
    return results


def scan_adr_arbitrage(cfg: dict) -> list[tuple[str, str]]:
    results = []
    threshold = cfg["thresholds"].get("adr_gap_pct", 2.0)
    usd_twd   = get_usd_twd()

    for pair in cfg.get("adr_pairs", []):
        try:
            tw_price  = get_current_price(pair["tw"])
            us_price  = get_current_price(pair["us"])
            ratio     = pair["ratio"]

            tw_equiv  = us_price * usd_twd / ratio   # 美股換算成台幣每股
            gap_pct   = (tw_equiv - tw_price) / tw_price * 100

            if abs(gap_pct) >= threshold:
                direction = "美股溢價" if gap_pct > 0 else "美股折價"
                results.append((
                    pair["tw"],
                    f"{pair['name']} {direction} {abs(gap_pct):.1f}%"
                    f"（台股 {tw_price:.1f}，美股換算 {tw_equiv:.1f}，匯率 {usd_twd:.2f}）"
                ))
        except Exception:
            continue

    return results


# ═══════════════════════════════════════════════════════════════
# 主程式
# ═══════════════════════════════════════════════════════════════

def main():
    cfg       = load_config()
    portfolio = load_portfolio()
    topic     = cfg["ntfy_topic"]
    watchlist = cfg["watchlist"]
    enabled   = cfg["signals"]
    t         = cfg["thresholds"]
    today     = datetime.today().strftime("%Y/%m/%d")

    print(f"\n{'═'*55}")
    print(f"  台股全方位掃描  {today}")
    print(f"{'═'*55}")

    # 開頭先送測試推播，確認推播機制正常
    send_web_push("🔄 掃描開始", f"台股掃描啟動 {today}")

    any_signal = False

    # ── 個股技術面 ──────────────────────────────────────────────
    print(f"\n【個股技術面】掃描 {len(watchlist)} 支")
    for ticker in watchlist:
        try:
            print(f"  {ticker}", end=" ", flush=True)
            signals = scan_stock(ticker, cfg)
            if signals:
                any_signal = True
                text = "\n".join(f"• {s}" for s in signals)
                print(f"→ {len(signals)} 個信號")
                notify(topic, f"📈 {ticker} 出現 {len(signals)} 個信號！",
                       f"日期：{today}\n\n{text}", priority="high")
            else:
                print("→ 無")
        except Exception as e:
            print(f"→ 錯誤：{e}")

    # ── 重大公告提醒 ──────────────────────────────────────────────
    if enabled.get("announcements"):
        try:
            print(f"\n【重大公告】查詢未來 {t['announce_days_ahead']} 天")
            for ticker, msg in scan_announcements(watchlist, t["announce_days_ahead"]):
                any_signal = True
                notify(topic, f"📅 {ticker} 重要日期提醒", msg, priority="high")
        except Exception as e:
            print(f"  重大公告模組錯誤：{e}")

    # ── 停損 / 停利 ────────────────────────────────────────────────
    if enabled.get("portfolio_alerts"):
        try:
            print(f"\n【持倉監控】{len(portfolio['holdings'])} 筆持倉")
            for ticker, label, msg in scan_portfolio_alerts(portfolio):
                any_signal = True
                priority = "urgent" if "停損" in label else "high"
                notify(topic, f"{label}  {ticker}", msg, priority=priority)
        except Exception as e:
            print(f"  持倉監控模組錯誤：{e}")

    # ── 籌碼面（外資 + 投信）────────────────────────────────────────
    if enabled.get("institutional"):
        try:
            print(f"\n【籌碼面】外資 + 投信")
            for ticker, msg in scan_institutional(watchlist, t):
                any_signal = True
                notify(topic, f"🏦 {ticker} 法人大買！", f"日期：{today}\n\n{msg}", priority="high")
        except Exception as e:
            print(f"  籌碼面模組錯誤：{e}")

    # ── ETF 折價套利 ───────────────────────────────────────────────
    if enabled.get("etf_arbitrage"):
        try:
            print(f"\n【ETF 折價】{len(cfg.get('etf_watchlist', []))} 支 ETF")
            for ticker, msg in scan_etf_arbitrage(cfg):
                any_signal = True
                notify(topic, f"💰 {ticker} ETF 折價機會！", f"日期：{today}\n\n{msg}", priority="high")
        except Exception as e:
            print(f"  ETF 套利模組錯誤：{e}")

    # ── ADR 套利 ──────────────────────────────────────────────────
    if enabled.get("adr_arbitrage"):
        try:
            print(f"\n【ADR 套利】{len(cfg.get('adr_pairs', []))} 組")
            for ticker, msg in scan_adr_arbitrage(cfg):
                any_signal = True
                notify(topic, f"🌏 {ticker} 美股套利機會！", f"日期：{today}\n\n{msg}", priority="high")
        except Exception as e:
            print(f"  ADR 套利模組錯誤：{e}")

    # ── 今日無信號 ────────────────────────────────────────────────
    if not any_signal:
        notify(topic, f"📊 掃描完成 ({today})",
               f"掃描了 {len(watchlist)} 支個股、{len(cfg.get('etf_watchlist', []))} 支 ETF，今日無明顯信號。")

    print(f"\n{'═'*55}")
    print(f"  掃描完成！")
    print(f"{'═'*55}\n")


if __name__ == "__main__":
    main()
