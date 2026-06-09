# -*- coding: utf-8 -*-
"""a-stock-data API Wrapper — 23 routes, 7 layers, 5 data sources, graceful degradation"""

import sys
import os
import time
import threading
from flask import Flask, request, jsonify

from stock_core import (
    eastmoney_concept_blocks,
    eastmoney_stock_info,
    eastmoney_fund_flow_minute,
    stock_fund_flow_120d,
    dragon_tiger_board,
    daily_dragon_tiger,
    industry_comparison,
    tencent_quote,
    baidu_kline_with_ma,
    ths_hot_reason,
    hsgt_realtime,
    eastmoney_reports,
    download_pdf,
    ths_eps_forecast,
    eastmoney_stock_news,
    eastmoney_global_news,
    margin_trading,
    block_trade,
    lockup_expiry,
    holder_num_change,
    dividend_history,
    sina_financial_report,
    cninfo_announcements,
    full_valuation,
    forward_pe,
    calc_peg,
    pe_digestion,
    iwencai_search,
    iwencai_query,
    dedup_articles,
)

app = Flask(__name__)


# ============================================================================
# Anti-ban infrastructure
# ============================================================================

_rate_stats = {
    "eastmoney_calls": 0,
    "last_em_call": 0.0,
    "start_time": time.time(),
}
_stats_lock = threading.Lock()

def _track_em_call():
    with _stats_lock:
        _rate_stats["eastmoney_calls"] += 1
        _rate_stats["last_em_call"] = time.time()

def _rate_limit_headers():
    with _stats_lock:
        uptime = time.time() - _rate_stats["start_time"]
        calls = _rate_stats["eastmoney_calls"]
    return {
        "X-RateLimit-EM-Calls": str(calls),
        "X-RateLimit-EM-Rate": f"{calls / max(uptime, 1):.2f}/s",
        "X-Uptime-Seconds": f"{uptime:.0f}",
    }


# ============================================================================
# Helpers
# ============================================================================

def _ok(data):
    resp = jsonify({"status": "success", "data": data})
    for k, v in _rate_limit_headers().items():
        resp.headers[k] = v
    return resp

def _err(msg: str, code: int = 400):
    resp = jsonify({"status": "error", "data": None, "message": msg})
    resp.status_code = code
    for k, v in _rate_limit_headers().items():
        resp.headers[k] = v
    return resp


# ============================================================================
# Route 0: Health + rate stats
# ============================================================================

@app.route("/api/v1/health", methods=["GET"])
def api_health():
    with _stats_lock:
        stats = dict(_rate_stats)
    stats["uptime_s"] = round(time.time() - stats["start_time"], 1)
    return _ok({"message": "a-stock-data API Wrapper is running", "rate_stats": stats})


# ============================================================================
# Route 1: Dragon Tiger Board
# ============================================================================

@app.route("/api/v1/stock/dragon_tiger", methods=["GET"])
def api_dragon_tiger():
    try:
        scope = request.args.get("scope", "single")
        trade_date = request.args.get("trade_date")
        if scope == "all":
            min_net_buy = request.args.get("min_net_buy", type=float)
            _track_em_call()
            result = daily_dragon_tiger(trade_date=trade_date, min_net_buy=min_net_buy)
            return _ok(result)
        code = request.args.get("code", "").strip()
        if not code or len(code) != 6 or not code.isdigit():
            return _err("参数 code 必须为6位数字股票代码")
        if trade_date is None:
            trade_date = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
        look_back = request.args.get("look_back", 30, type=int)
        _track_em_call()
        result = dragon_tiger_board(code=code, trade_date=trade_date, look_back=look_back)
        return _ok(result)
    except Exception as e:
        print(f"[WARN] Dragon tiger: {e}")
        return _ok({"records": [], "seats": {"buy": [], "sell": []},
                     "institution": {"buy_amt": 0, "sell_amt": 0, "net_amt": 0},
                     "note": "数据源暂时不可用"})


# ============================================================================
# Route 2: Concept Blocks (东财 slist，V3.2.2 #18)
# ============================================================================

@app.route("/api/v1/stock/concept", methods=["GET"])
def api_concept():
    try:
        code = request.args.get("code", "").strip()
        if not code or len(code) != 6 or not code.isdigit():
            return _err("参数 code 必须为6位数字股票代码")
        _track_em_call()
        result = eastmoney_concept_blocks(code)
        if result.get("total", 0) > 0:
            return _ok(result)
        raise RuntimeError("东财 slist 返回空")
    except Exception as e:
        print(f"[WARN] Concept: {e}")
        try:
            _track_em_call()
            info = eastmoney_stock_info(code)
            if info and info.get("industry"):
                industry = info["industry"]
                return _ok({
                    "total": 1,
                    "boards": [{"name": industry, "code": "", "change_pct": "", "lead_stock": ""}],
                    "concept_tags": [industry],
                    "note": "仅返回行业信息（板块归属接口受限）",
                })
        except Exception:
            pass
        return _ok({"total": 0, "boards": [], "concept_tags": [], "note": "数据源受限"})


# ============================================================================
# Route 3: Fund Flow
# ============================================================================

@app.route("/api/v1/stock/fund_flow", methods=["GET"])
def api_fund_flow():
    try:
        code = request.args.get("code", "").strip()
        if not code or len(code) != 6 or not code.isdigit():
            return _err("参数 code 必须为6位数字股票代码")
        period = request.args.get("period", "minute")
        _track_em_call()
        result = stock_fund_flow_120d(code) if period == "120d" else eastmoney_fund_flow_minute(code)
        return _ok({"code": code, "period": period, "records": result})
    except Exception as e:
        print(f"[WARN] Fund flow: {e}")
        return _ok({"code": request.args.get("code", ""), "period": request.args.get("period", "minute"),
                     "records": [], "note": "数据源暂时不可用"})


# ============================================================================
# Route 4: Industry Comparison
# ============================================================================

@app.route("/api/v1/stock/industry", methods=["GET"])
def api_industry():
    try:
        top_n = request.args.get("top_n", 20, type=int)
        _track_em_call()
        return _ok(industry_comparison(top_n=top_n))
    except Exception as e:
        print(f"[WARN] Industry: {e}")
        return _ok({"top": [], "bottom": [], "total": 0, "note": "数据源暂时不可用"})


# ============================================================================
# Route 5: Tencent Quote (never banned)
# ============================================================================

@app.route("/api/v1/stock/quote", methods=["GET"])
def api_quote():
    try:
        codes_str = request.args.get("codes", "").strip()
        if not codes_str:
            return _err("参数 codes 为必填，逗号分隔")
        codes = [c.strip() for c in codes_str.split(",") if c.strip()]
        if not codes:
            return _err("参数 codes 格式错误")
        return _ok(tencent_quote(codes))
    except Exception as e:
        print(f"[WARN] Quote: {e}")
        return _ok({"note": "行情数据源暂时不可用"})


# ============================================================================
# Route 6: Baidu K-line with MA
# ============================================================================

@app.route("/api/v1/stock/kline", methods=["GET"])
def api_kline():
    try:
        code = request.args.get("code", "").strip()
        if not code or len(code) != 6 or not code.isdigit():
            return _err("参数 code 必须为6位数字股票代码")
        start_time = request.args.get("start_time", "")
        return _ok(baidu_kline_with_ma(code, start_time=start_time))
    except Exception as e:
        print(f"[WARN] Kline: {e}")
        return _ok({"keys": [], "rows": [], "note": "数据源暂时不可用"})


# ============================================================================
# Route 7: Hot Stocks with Reason Tags
# ============================================================================

@app.route("/api/v1/stock/hot", methods=["GET"])
def api_hot():
    try:
        date = request.args.get("date") or None
        result = ths_hot_reason(date=date)
        return _ok({"date": date, "count": len(result), "stocks": result})
    except Exception as e:
        print(f"[WARN] Hot: {e}")
        return _ok({"date": request.args.get("date"), "count": 0, "stocks": [],
                     "note": "数据源暂时不可用"})


# ============================================================================
# Route 8: Northbound Flow
# ============================================================================

@app.route("/api/v1/stock/northbound", methods=["GET"])
def api_northbound():
    try:
        return _ok(hsgt_realtime())
    except Exception as e:
        print(f"[WARN] Northbound: {e}")
        return _ok({"records": [], "summary": {"hgt_yi": 0, "sgt_yi": 0, "total_yi": 0},
                     "note": "数据源暂时不可用"})


# ============================================================================
# Route 9: Research Reports
# ============================================================================

@app.route("/api/v1/stock/reports", methods=["GET"])
def api_reports():
    try:
        code = request.args.get("code", "").strip()
        if not code or len(code) != 6 or not code.isdigit():
            return _err("参数 code 必须为6位数字股票代码")
        max_pages = request.args.get("max_pages", 5, type=int)
        _track_em_call()
        result = eastmoney_reports(code, max_pages=max_pages)
        return _ok({"code": code, "count": len(result), "reports": result})
    except Exception as e:
        print(f"[WARN] Reports: {e}")
        return _ok({"code": request.args.get("code", ""), "count": 0, "reports": [],
                     "note": "数据源暂时不可用"})


# ============================================================================
# Route 10: Consensus EPS Forecast
# ============================================================================

@app.route("/api/v1/stock/eps_forecast", methods=["GET"])
def api_eps_forecast():
    try:
        code = request.args.get("code", "").strip()
        if not code or len(code) != 6 or not code.isdigit():
            return _err("参数 code 必须为6位数字股票代码")
        result = ths_eps_forecast(code)
        return _ok({"code": code, "forecast": result})
    except Exception as e:
        print(f"[WARN] EPS forecast: {e}")
        return _ok({"code": request.args.get("code", ""), "forecast": [],
                     "note": "无机构覆盖或数据源暂时不可用"})


# ============================================================================
# Route 11: Stock News
# ============================================================================

@app.route("/api/v1/stock/news", methods=["GET"])
def api_news():
    try:
        code = request.args.get("code", "").strip()
        if not code or len(code) != 6 or not code.isdigit():
            return _err("参数 code 必须为6位数字股票代码")
        page_size = request.args.get("page_size", 20, type=int)
        _track_em_call()
        result = eastmoney_stock_news(code, page_size=page_size)
        return _ok({"code": code, "count": len(result), "news": result})
    except Exception as e:
        print(f"[WARN] News: {e}")
        return _ok({"code": request.args.get("code", ""), "count": 0, "news": [],
                     "note": "数据源暂时不可用"})


# ============================================================================
# Route 12: Global News
# ============================================================================

@app.route("/api/v1/stock/global_news", methods=["GET"])
def api_global_news():
    try:
        page_size = request.args.get("page_size", 50, type=int)
        _track_em_call()
        result = eastmoney_global_news(page_size=page_size)
        return _ok({"count": len(result), "news": result})
    except Exception as e:
        print(f"[WARN] Global news: {e}")
        return _ok({"count": 0, "news": [], "note": "数据源暂时不可用"})


# ============================================================================
# Route 13: Margin Trading
# ============================================================================

@app.route("/api/v1/stock/margin", methods=["GET"])
def api_margin():
    try:
        code = request.args.get("code", "").strip()
        if not code or len(code) != 6 or not code.isdigit():
            return _err("参数 code 必须为6位数字股票代码")
        page_size = request.args.get("page_size", 30, type=int)
        _track_em_call()
        result = margin_trading(code, page_size=page_size)
        return _ok({"code": code, "count": len(result), "records": result})
    except Exception as e:
        print(f"[WARN] Margin: {e}")
        return _ok({"code": request.args.get("code", ""), "count": 0, "records": [],
                     "note": "数据源暂时不可用"})


# ============================================================================
# Route 14: Block Trades
# ============================================================================

@app.route("/api/v1/stock/block_trade", methods=["GET"])
def api_block_trade():
    try:
        code = request.args.get("code", "").strip()
        if not code or len(code) != 6 or not code.isdigit():
            return _err("参数 code 必须为6位数字股票代码")
        page_size = request.args.get("page_size", 20, type=int)
        _track_em_call()
        result = block_trade(code, page_size=page_size)
        return _ok({"code": code, "count": len(result), "records": result})
    except Exception as e:
        print(f"[WARN] Block trade: {e}")
        return _ok({"code": request.args.get("code", ""), "count": 0, "records": [],
                     "note": "数据源暂时不可用"})


# ============================================================================
# Route 15: Lockup Expiry
# ============================================================================

@app.route("/api/v1/stock/lockup", methods=["GET"])
def api_lockup():
    try:
        code = request.args.get("code", "").strip()
        if not code or len(code) != 6 or not code.isdigit():
            return _err("参数 code 必须为6位数字股票代码")
        trade_date = request.args.get("trade_date") or __import__("datetime").datetime.now().strftime("%Y-%m-%d")
        forward_days = request.args.get("forward_days", 90, type=int)
        _track_em_call()
        result = lockup_expiry(code, trade_date, forward_days=forward_days)
        return _ok(result)
    except Exception as e:
        print(f"[WARN] Lockup: {e}")
        return _ok({"history": [], "upcoming": [], "note": "数据源暂时不可用"})


# ============================================================================
# Route 16: Shareholder Count
# ============================================================================

@app.route("/api/v1/stock/holder", methods=["GET"])
def api_holder():
    try:
        code = request.args.get("code", "").strip()
        if not code or len(code) != 6 or not code.isdigit():
            return _err("参数 code 必须为6位数字股票代码")
        page_size = request.args.get("page_size", 20, type=int)
        _track_em_call()
        result = holder_num_change(code, page_size=page_size)
        return _ok({"code": code, "count": len(result), "records": result})
    except Exception as e:
        print(f"[WARN] Holder: {e}")
        return _ok({"code": request.args.get("code", ""), "count": 0, "records": [],
                     "note": "数据源暂时不可用"})


# ============================================================================
# Route 17: Dividend History
# ============================================================================

@app.route("/api/v1/stock/dividend", methods=["GET"])
def api_dividend():
    try:
        code = request.args.get("code", "").strip()
        if not code or len(code) != 6 or not code.isdigit():
            return _err("参数 code 必须为6位数字股票代码")
        page_size = request.args.get("page_size", 20, type=int)
        _track_em_call()
        result = dividend_history(code, page_size=page_size)
        return _ok({"code": code, "count": len(result), "records": result})
    except Exception as e:
        print(f"[WARN] Dividend: {e}")
        return _ok({"code": request.args.get("code", ""), "count": 0, "records": [],
                     "note": "数据源暂时不可用"})


# ============================================================================
# Route 18: Financial Reports (Sina)
# ============================================================================

@app.route("/api/v1/stock/financial", methods=["GET"])
def api_financial():
    try:
        code = request.args.get("code", "").strip()
        if not code or len(code) != 6 or not code.isdigit():
            return _err("参数 code 必须为6位数字股票代码")
        report_type = request.args.get("type", "lrb")
        if report_type not in ("lrb", "fzb", "llb"):
            return _err("参数 type 必须为 lrb(利润表)/fzb(资产负债表)/llb(现金流量表)")
        num = request.args.get("num", 8, type=int)
        result = sina_financial_report(code, report_type=report_type, num=num)
        return _ok({"code": code, "type": report_type, "count": len(result), "records": result})
    except Exception as e:
        print(f"[WARN] Financial: {e}")
        return _ok({"code": request.args.get("code", ""), "type": request.args.get("type", "lrb"),
                     "count": 0, "records": [], "note": "数据源暂时不可用"})


# ============================================================================
# Route 19: Announcements (cninfo)
# ============================================================================

@app.route("/api/v1/stock/announcements", methods=["GET"])
def api_announcements():
    try:
        code = request.args.get("code", "").strip()
        if not code or len(code) != 6 or not code.isdigit():
            return _err("参数 code 必须为6位数字股票代码")
        page_size = request.args.get("page_size", 30, type=int)
        result = cninfo_announcements(code, page_size=page_size)
        return _ok({"code": code, "count": len(result), "announcements": result})
    except Exception as e:
        print(f"[WARN] Announcements: {e}")
        return _ok({"code": request.args.get("code", ""), "count": 0, "announcements": [],
                     "note": "数据源暂时不可用"})


# ============================================================================
# Route 20: Full Valuation
# ============================================================================

@app.route("/api/v1/stock/valuation", methods=["GET"])
def api_valuation():
    try:
        code = request.args.get("code", "").strip()
        if not code or len(code) != 6 or not code.isdigit():
            return _err("参数 code 必须为6位数字股票代码")
        result = full_valuation(code)
        return _ok(result)
    except Exception as e:
        print(f"[WARN] Valuation: {e}")
        return _ok({"note": f"估值计算失败: {str(e)}"})


# ============================================================================
# Route 21: PEG Calculator
# ============================================================================

@app.route("/api/v1/stock/peg", methods=["GET"])
def api_peg():
    try:
        pe = request.args.get("pe", type=float)
        cagr = request.args.get("cagr", type=float)
        if pe is None or cagr is None:
            return _err("参数 pe 和 cagr 为必填")
        peg = calc_peg(pe, cagr)
        digest = pe_digestion(pe, cagr)
        return _ok({"pe": pe, "cagr_pct": cagr * 100 if cagr else 0,
                     "peg": round(peg, 2) if peg != float("inf") else None,
                     "digest_years": round(digest, 1)})
    except Exception as e:
        print(f"[WARN] PEG: {e}")
        return _ok({"note": f"计算失败: {str(e)}"})


# ============================================================================
# Route 22: iwencai Semantic Search (requires API key)
# ============================================================================

@app.route("/api/v1/stock/iwencai/search", methods=["GET"])
def api_iwencai_search():
    try:
        query = request.args.get("query", "").strip()
        if not query:
            return _err("参数 query 为必填")
        channel = request.args.get("channel", "report")
        size = request.args.get("size", 50, type=int)
        result = iwencai_search(query, channel=channel, size=size)
        return _ok({"query": query, "channel": channel, "count": len(result), "articles": result})
    except Exception as e:
        print(f"[WARN] iwencai search: {e}")
        return _ok({"query": request.args.get("query", ""), "count": 0, "articles": [],
                     "note": f"搜索失败（可能需要 IWENCAI_API_KEY）: {str(e)}"})


# ============================================================================
# Route 23: iwencai Structured Query (requires API key)
# ============================================================================

@app.route("/api/v1/stock/iwencai/query", methods=["GET"])
def api_iwencai_query():
    try:
        query = request.args.get("query", "").strip()
        if not query:
            return _err("参数 query 为必填")
        page = request.args.get("page", 1, type=int)
        limit = request.args.get("limit", 50, type=int)
        result = iwencai_query(query, page=page, limit=limit)
        return _ok({"query": query, "count": len(result), "data": result})
    except Exception as e:
        print(f"[WARN] iwencai query: {e}")
        return _ok({"query": request.args.get("query", ""), "count": 0, "data": [],
                     "note": f"查询失败（可能需要 IWENCAI_API_KEY）: {str(e)}"})


# ============================================================================
# Startup
# ============================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 9000))
    app.run(host="0.0.0.0", port=port, debug=False)
