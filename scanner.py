import json
import os
import requests
import yfinance as yf
import pandas as pd
import google.generativeai as genai
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator, MACD
from ta.volatility import BollingerBands
from datetime import datetime, timedelta


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
# AI 分析
# ═══════════════════════════════════════════════════════════════

def analyze_with_ai(ticker: str, price: float, change_pct: float, signals: list) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return {"verdict": "未設定", "reason": "請新增 GEMINI_API_KEY 到 GitHub Secrets"}

    signal_text = "\n".join(f"- {s}" for s in signals)
    prompt = f"""你是台股技術分析師，根據以下資訊給出投資建議。

股票：{ticker}
現價：{price}，今日漲跌：{change_pct:+.2f}%
偵測到的信號：
{signal_text}

請只回覆以下格式，不要其他文字：
建議：[建議買入 / 觀望 / 避開]
理由：[一句話，不超過25字]"""

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash-lite")
        response = model.generate_content(prompt)
        text = response.text.strip()
        verdict, reason = "觀望", ""
        for line in text.splitlines():
            if line.startswith("建議："):
                verdict = line.replace("建議：", "").strip()
            elif line.startswith("理由："):
                reason = line.replace("理由：", "").strip()
        return {"verdict": verdict, "reason": reason}
    except Exception as e:
        print(f"    AI 分析失敗：{e}")
        return {"verdict": "分析失敗", "reason": str(e)[:40]}


# ═══════════════════════════════════════════════════════════════
# 推播（僅 ntfy 手機）
# ═══════════════════════════════════════════════════════════════

def notify(topic: str, title: str, message: str, priority: str = "default"):
    print(f"  📣 {title}")
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
    macd_line   = MACD(c).macd()
    signal_line = MACD(c).macd_signal()
    return bool((macd_line.iloc[-2] < signal_line.iloc[-2]) and (macd_line.iloc[-1] > signal_line.iloc[-1]))


def check_bollinger_bounce(df: pd.DataFrame, threshold: float) -> bool:
    if len(df) < 22:
        return False
    pband = BollingerBands(df["Close"].squeeze(), window=20, window_dev=2).bollinger_pband()
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
# 各模組掃描（回傳結果 list，不直接推播）
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


def scan_announcements(watchlist: list, days_ahead: int) -> list[dict]:
    results = []
    today    = datetime.today().date()
    deadline = today + timedelta(days=days_ahead)
    for ticker in watchlist:
        try:
            cal = yf.Ticker(ticker).calendar
            if cal is None or cal.empty:
                continue
            if "Ex-Dividend Date" in cal.index:
                ex_div = pd.Timestamp(cal.loc["Ex-Dividend Date"].iloc[0]).date()
                if today <= ex_div <= deadline:
                    results.append({"ticker": ticker, "type": "除息", "message": f"除息日：{ex_div}（{(ex_div - today).days} 天後）"})
            if "Earnings Date" in cal.index:
                earn = pd.Timestamp(cal.loc["Earnings Date"].iloc[0]).date()
                if today <= earn <= deadline:
                    results.append({"ticker": ticker, "type": "法說會", "message": f"財報/法說：{earn}（{(earn - today).days} 天後）"})
        except Exception:
            continue
    return results


def scan_portfolio_alerts(portfolio: dict) -> list[dict]:
    results = []
    stop_loss   = portfolio.get("stop_loss_pct", -8.0)
    take_profit = portfolio.get("take_profit_pct", 20.0)
    for h in portfolio.get("holdings", []):
        price = get_current_price(h["ticker"])
        if price <= 0:
            continue
        pct = (price - h["buy_price"]) / h["buy_price"] * 100
        status = "正常"
        if pct <= stop_loss:
            status = "停損"
        elif pct >= take_profit:
            status = "停利"
        results.append({
            "ticker":    h["ticker"],
            "name":      h.get("name", h["ticker"]),
            "buy_price": h["buy_price"],
            "now_price": round(price, 2),
            "pct":       round(pct, 2),
            "status":    status,
        })
    return results


def scan_institutional(watchlist: list, thresholds: dict) -> list[dict]:
    results = []
    f_min  = thresholds.get("foreign_net_buy_million", 500)  * 1_000_000
    tr_min = thresholds.get("trust_net_buy_million",   200)  * 1_000_000
    for ticker in watchlist:
        data = get_institutional_net(ticker.replace(".TW", ""))
        if not data:
            continue
        msgs = []
        if data.get("foreign_net", 0) >= f_min:
            msgs.append(f"外資買超 {data['foreign_net']/1_000_000:.0f} 百萬股")
        if data.get("trust_net", 0) >= tr_min:
            msgs.append(f"投信買超 {data['trust_net']/1_000_000:.0f} 百萬股")
        if msgs:
            results.append({"ticker": ticker, "message": "、".join(msgs)})
    return results


def scan_etf_arbitrage(cfg: dict) -> list[dict]:
    results = []
    threshold = cfg["thresholds"]["etf_discount_threshold"]
    for ticker in cfg.get("etf_watchlist", []):
        df = fetch_ohlcv(ticker, days=5)
        if df.empty:
            continue
        price = float(df["Close"].iloc[-1])
        nav   = get_etf_nav(ticker.replace(".TW", ""))
        if nav <= 0:
            continue
        discount = round((price - nav) / nav * 100, 2)
        if discount <= threshold:
            results.append({"ticker": ticker, "price": price, "nav": nav, "discount_pct": discount})
    return results


def scan_adr_arbitrage(cfg: dict) -> list[dict]:
    results = []
    threshold = cfg["thresholds"].get("adr_gap_pct", 2.0)
    usd_twd   = get_usd_twd()
    for pair in cfg.get("adr_pairs", []):
        try:
            tw_price = get_current_price(pair["tw"])
            us_price = get_current_price(pair["us"])
            tw_equiv = us_price * usd_twd / pair["ratio"]
            gap_pct  = round((tw_equiv - tw_price) / tw_price * 100, 2)
            if abs(gap_pct) >= threshold:
                results.append({
                    "name":     pair["name"],
                    "tw":       pair["tw"],
                    "us":       pair["us"],
                    "tw_price": round(tw_price, 2),
                    "us_equiv": round(tw_equiv, 2),
                    "gap_pct":  gap_pct,
                    "usd_twd":  round(usd_twd, 2),
                })
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
    now       = datetime.now()
    today_str = now.strftime("%Y/%m/%d %H:%M")

    print(f"\n{'═'*55}")
    print(f"  台股全方位掃描  {today_str}")
    print(f"{'═'*55}")

    etf_watchlist = cfg.get("etf_watchlist", [])
    all_tickers   = watchlist + etf_watchlist

    results = {
        "scan_time":         today_str,
        "stocks_scanned":    len(all_tickers),
        "etfs_scanned":      len(etf_watchlist),
        "all_stocks":        [],
        "technical_signals": [],
        "announcements":     [],
        "portfolio":         [],
        "institutional":     [],
        "etf_arbitrage":     [],
        "adr_arbitrage":     [],
    }

    # ── 所有股票概覽（個股 + ETF 合一）──
    print(f"\n【全部股票掃描】共 {len(all_tickers)} 支")
    for ticker in all_tickers:
        try:
            print(f"  {ticker}", end=" ", flush=True)
            df = fetch_ohlcv(ticker)
            if df.empty or len(df) < 2:
                print("→ 無資料")
                continue

            price     = round(float(df["Close"].iloc[-1]), 2)
            prev      = round(float(df["Close"].iloc[-2]), 2)
            change    = round(price - prev, 2)
            change_pct= round((price - prev) / prev * 100, 2) if prev else 0
            is_etf    = ticker in etf_watchlist

            signals = []
            if not is_etf:
                signals = scan_stock(ticker, cfg)
            else:
                signals = scan_stock(ticker, cfg)

            ai = {"verdict": "—", "reason": ""}
            if signals:
                print(f"→ 現價 {price}（{change_pct:+.2f}%），{len(signals)} 個信號，AI 分析中...", flush=True)
                ai = analyze_with_ai(ticker, price, change_pct, signals)
                results["technical_signals"].append({"ticker": ticker, "signals": signals, "ai": ai})
                notify(topic, f"📈 {ticker} {ai['verdict']}",
                       f"日期：{today_str}\nAI：{ai['verdict']} — {ai['reason']}\n\n" +
                       "\n".join(f"• {s}" for s in signals), priority="high")
            else:
                print(f"→ 現價 {price}（{change_pct:+.2f}%）")

            prices_20d = [round(float(v), 2) for v in df["Close"].iloc[-20:].tolist()]

            stock_info = {
                "ticker":     ticker,
                "is_etf":     is_etf,
                "price":      price,
                "change":     change,
                "change_pct": change_pct,
                "signals":    signals,
                "ai":         ai,
                "prices_20d": prices_20d,
            }
            results["all_stocks"].append(stock_info)
        except Exception as e:
            print(f"→ 錯誤：{e}")

    # ── 重大公告 ──
    if enabled.get("announcements"):
        try:
            print(f"\n【重大公告】")
            items = scan_announcements(watchlist, t["announce_days_ahead"])
            results["announcements"] = items
            for item in items:
                notify(topic, f"📅 {item['ticker']} {item['type']}提醒", item["message"], priority="high")
        except Exception as e:
            print(f"  錯誤：{e}")

    # ── 持倉監控 ──
    if enabled.get("portfolio_alerts"):
        try:
            print(f"\n【持倉監控】")
            items = scan_portfolio_alerts(portfolio)
            results["portfolio"] = items
            for item in items:
                if item["status"] in ("停損", "停利"):
                    emoji = "🔴" if item["status"] == "停損" else "🟢"
                    notify(topic,
                           f"{emoji} {item['status']} {item['ticker']}",
                           f"{item['name']}：{item['pct']:+.1f}%（買入 {item['buy_price']}，現價 {item['now_price']}）",
                           priority="urgent" if item["status"] == "停損" else "high")
        except Exception as e:
            print(f"  錯誤：{e}")

    # ── 籌碼面 ──
    if enabled.get("institutional"):
        try:
            print(f"\n【籌碼面】")
            items = scan_institutional(watchlist, t)
            results["institutional"] = items
            for item in items:
                notify(topic, f"🏦 {item['ticker']} 法人大買！",
                       f"日期：{today_str}\n\n{item['message']}", priority="high")
        except Exception as e:
            print(f"  錯誤：{e}")

    # ── ETF 套利 ──
    if enabled.get("etf_arbitrage"):
        try:
            print(f"\n【ETF 折價】")
            items = scan_etf_arbitrage(cfg)
            results["etf_arbitrage"] = items
            for item in items:
                notify(topic, f"💰 {item['ticker']} ETF 折價機會！",
                       f"市價 {item['price']} vs 淨值 {item['nav']}，折價 {item['discount_pct']}%", priority="high")
        except Exception as e:
            print(f"  錯誤：{e}")

    # ── ADR 套利 ──
    if enabled.get("adr_arbitrage"):
        try:
            print(f"\n【ADR 套利】")
            items = scan_adr_arbitrage(cfg)
            results["adr_arbitrage"] = items
            for item in items:
                direction = "溢價" if item["gap_pct"] > 0 else "折價"
                notify(topic, f"🌏 {item['name']} 美股{direction}機會！",
                       f"台股 {item['tw_price']} vs 美股換算 {item['us_equiv']}，差距 {item['gap_pct']:+.1f}%", priority="high")
        except Exception as e:
            print(f"  錯誤：{e}")

    # ── 今日無信號 ──
    total_signals = (len(results["technical_signals"]) + len(results["announcements"]) +
                     sum(1 for p in results["portfolio"] if p["status"] != "正常") +
                     len(results["institutional"]) + len(results["etf_arbitrage"]) + len(results["adr_arbitrage"]))

    if total_signals == 0:
        notify(topic, f"📊 掃描完成 ({today_str})",
               f"掃描了 {len(watchlist)} 支個股、{len(cfg.get('etf_watchlist', []))} 支 ETF，今日無明顯信號。")

    # ── 存結果到網頁 ──
    os.makedirs("docs", exist_ok=True)
    with open("docs/results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n結果已寫入 docs/results.json")
    print(f"{'═'*55}\n")


if __name__ == "__main__":
    main()
