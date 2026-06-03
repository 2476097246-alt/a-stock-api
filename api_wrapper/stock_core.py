# -*- coding: utf-8 -*-
"""
a-stock-data 核心数据抓取模块
从 SKILL.md 抽取，供 api_wrapper Flask 调用
所有东财接口通过 em_get() 统一节流，防止 IP 被封
"""

import time
import random
import requests
from datetime import datetime, timedelta
import os
import json
import re
import secrets
import uuid
import math
from pathlib import Path
from io import StringIO
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


# ============================================================================
# 全局配置
# ============================================================================

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

REPORT_API = "https://reportapi.eastmoney.com/report/list"
PDF_TPL = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"
IWENCAI_BASE = os.environ.get("IWENCAI_BASE_URL", "https://openapi.iwencai.com")
IWENCAI_KEY = os.environ.get("IWENCAI_API_KEY", "")

# 东财防封：全局节流 + 会话复用
EM_SESSION = requests.Session()
EM_SESSION.headers.update({"User-Agent": UA})
EM_MIN_INTERVAL = 1.0          # 两次东财请求最小间隔(秒)；批量筛选建议调大到 1.5~2
_em_last_call = [0.0]          # 模块级上次请求时间戳

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

# 百度股市通 PAE 请求头
_BAIDU_PAE_HEADERS = {
    "Host": "finance.pae.baidu.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0",
    "Accept": "application/vnd.finance-web.v1+json",
    "Origin": "https://gushitong.baidu.com",
    "Referer": "https://gushitong.baidu.com/",
}


# ============================================================================
# 东财统一请求入口（防封）
# ============================================================================

def em_get(url: str, params: dict | None = None, headers: dict | None = None,
           timeout: int = 15, **kwargs):
    """东财统一请求入口：自动节流 + 复用 session + 默认 UA"""
    wait = EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.5))
    try:
        return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
    finally:
        _em_last_call[0] = time.time()


def eastmoney_datacenter(report_name: str, columns: str = "ALL",
                          filter_str: str = "", page_size: int = 50,
                          sort_columns: str = "", sort_types: str = "-1") -> list[dict]:
    """东财数据中心统一查询 — 龙虎榜/解禁/融资融券/大宗交易/股东户数/分红 共用"""
    params = {
        "reportName": report_name, "columns": columns,
        "filter": filter_str, "pageNumber": "1", "pageSize": str(page_size),
        "sortColumns": sort_columns, "sortTypes": sort_types,
        "source": "WEB", "client": "WEB",
    }
    r = em_get(DATACENTER_URL, params=params, timeout=15)
    d = r.json()
    if d.get("result") and d["result"].get("data"):
        return d["result"]["data"]
    return []


# ============================================================================
# 1. 概念板块归属（百度股市通）
# ============================================================================

def baidu_concept_blocks(code: str) -> dict:
    """
    百度股市通概念板块归属。
    返回: {industry: [...], concept: [...], region: [...], concept_tags: [...]}
    """
    url = (
        f"https://finance.pae.baidu.com/api/getrelatedblock"
        f"?code={code}&market=ab"
        f"&typeCode=all&finClientType=pc"
    )
    r = requests.get(url, headers=_BAIDU_PAE_HEADERS, timeout=10)
    d = r.json()
    if str(d.get("ResultCode", -1)) != "0":
        raise RuntimeError(f"百度PAE错误: {d}")

    result = {"industry": [], "concept": [], "region": [], "concept_tags": []}
    for block in d.get("Result", []):
        block_type = block.get("type", "")
        for item in block.get("list", []):
            entry = {
                "name": item.get("name", ""),
                "change_pct": item.get("increase", ""),
                "desc": item.get("desc", ""),
            }
            if "行业" in block_type:
                result["industry"].append(entry)
            elif "概念" in block_type:
                result["concept"].append(entry)
                result["concept_tags"].append(entry["name"])
            elif "地域" in block_type:
                result["region"].append(entry)
    return result



# ============================================================================
# 1b. ?????????? push2 ? ?????????
# ============================================================================

def eastmoney_stock_info(code: str) -> dict:
    """
    ???????? push2??
    ??: {code, name, industry}  ?  {}
    ???????????????????
    """
    secid = f"1.{code}" if code.startswith("6") else f"0.{code}"
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "secid": secid,
        "fields": "f57,f58,f100",
    }
    headers = {"User-Agent": UA}
    try:
        r = em_get(url, params=params, headers=headers, timeout=10)
        d = r.json()
        data = d.get("data") or {}
        industry = data.get("f100", "")
        if industry == "-":
            industry = ""
        return {
            "code": data.get("f57", code),
            "name": data.get("f58", ""),
            "industry": industry,
        }
    except Exception:
        return {}

# ============================================================================
# 2. 个股资金流向（分钟级，东财 push2）
# ============================================================================

def eastmoney_fund_flow_minute(code: str) -> list[dict]:
    """
    个股资金流向（分钟级，当日盘中）。
    code: 6位股票代码
    返回: [{time, main_net, small_net, mid_net, large_net, super_net}, ...]
    单位: 元
    """
    secid = f"1.{code}" if code.startswith("6") else f"0.{code}"
    url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
    params = {
        "secid": secid, "klt": 1,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
    }
    headers = {
        "User-Agent": UA,
        "Referer": "https://quote.eastmoney.com/",
        "Origin": "https://quote.eastmoney.com",
    }
    r = em_get(url, params=params, headers=headers, timeout=10)
    d = r.json()

    rows = []
    for line in d.get("data", {}).get("klines", []):
        parts = line.split(",")
        if len(parts) >= 6:
            rows.append({
                "time": parts[0],
                "main_net": float(parts[1]),
                "small_net": float(parts[2]),
                "mid_net": float(parts[3]),
                "large_net": float(parts[4]),
                "super_net": float(parts[5]),
            })
    return rows


# ============================================================================
# 3. 个股资金流（120日，日级，东财 push2his）
# ============================================================================

def stock_fund_flow_120d(code: str) -> list[dict]:
    """
    个股资金流（日级，最近120个交易日）。
    返回: [{date, main_net(主力净流入), small_net, mid_net, large_net, super_net}]
    单位: 元
    """
    market_code = 1 if code.startswith("6") else 0
    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "secid": f"{market_code}.{code}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "lmt": "120",
    }
    headers = {
        "User-Agent": UA,
        "Referer": "https://quote.eastmoney.com/",
        "Origin": "https://quote.eastmoney.com",
    }
    r = em_get(url, params=params, headers=headers, timeout=15)
    d = r.json()
    klines = d.get("data", {}).get("klines", [])

    rows = []
    for line in klines:
        parts = line.split(",")
        if len(parts) >= 7:
            rows.append({
                "date": parts[0],
                "main_net": float(parts[1]) if parts[1] != "-" else 0,
                "small_net": float(parts[2]) if parts[2] != "-" else 0,
                "mid_net": float(parts[3]) if parts[3] != "-" else 0,
                "large_net": float(parts[4]) if parts[4] != "-" else 0,
                "super_net": float(parts[5]) if parts[5] != "-" else 0,
            })
    return rows


# ============================================================================
# 4. 龙虎榜席位 — 个股上榜记录 + 买卖席位 TOP5 + 机构动向
# ============================================================================

def dragon_tiger_board(code: str, trade_date: str, look_back: int = 30) -> dict:
    """
    龙虎榜数据聚合。
    trade_date: YYYY-MM-DD
    look_back: 回看天数
    返回: {records: [...], seats: {buy: [...], sell: [...]}, institution: {...}}
    """
    start = datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=look_back)
    start_str = start.strftime("%Y-%m-%d")

    # 1. 上榜记录
    records = []
    data = eastmoney_datacenter(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        filter_str=f"(TRADE_DATE>='{start_str}')(TRADE_DATE<='{trade_date}')(SECURITY_CODE=\"{code}\")",
        page_size=50,
        sort_columns="TRADE_DATE", sort_types="-1",
    )
    for row in data:
        records.append({
            "date": str(row.get("TRADE_DATE", ""))[:10],
            "reason": row.get("EXPLANATION", ""),
            "net_buy": round((row.get("BILLBOARD_NET_AMT") or 0) / 10000, 1),
            "turnover": round(float(row.get("TURNOVERRATE") or 0), 2),
        })

    # 2. 最近上榜的买卖席位
    seats = {"buy": [], "sell": []}
    buy_data = []
    sell_data = []
    if records:
        latest_date = records[0]["date"]
        # 买入席位
        buy_data = eastmoney_datacenter(
            "RPT_BILLBOARD_DAILYDETAILSBUY",
            filter_str=f"(TRADE_DATE='{latest_date}')(SECURITY_CODE=\"{code}\")",
            page_size=10,
            sort_columns="BUY", sort_types="-1",
        )
        for row in buy_data[:5]:
            seats["buy"].append({
                "name": row.get("OPERATEDEPT_NAME", ""),
                "buy_amt": round((row.get("BUY") or 0) / 10000, 1),
                "sell_amt": round((row.get("SELL") or 0) / 10000, 1),
                "net": round((row.get("NET") or 0) / 10000, 1),
            })
        # 卖出席位
        sell_data = eastmoney_datacenter(
            "RPT_BILLBOARD_DAILYDETAILSSELL",
            filter_str=f"(TRADE_DATE='{latest_date}')(SECURITY_CODE=\"{code}\")",
            page_size=10,
            sort_columns="SELL", sort_types="-1",
        )
        for row in sell_data[:5]:
            seats["sell"].append({
                "name": row.get("OPERATEDEPT_NAME", ""),
                "buy_amt": round((row.get("BUY") or 0) / 10000, 1),
                "sell_amt": round((row.get("SELL") or 0) / 10000, 1),
                "net": round((row.get("NET") or 0) / 10000, 1),
            })

    # 3. 机构买卖统计（从买卖席位明细中筛选 OPERATEDEPT_CODE="0" 即机构专用席位）
    institution = {"buy_amt": 0, "sell_amt": 0, "net_amt": 0}
    for detail_data, side in [(buy_data, "buy"), (sell_data, "sell")]:
        for row in detail_data:
            if str(row.get("OPERATEDEPT_CODE", "")) == "0":
                amt = (row.get("BUY") or 0) if side == "buy" else (row.get("SELL") or 0)
                if side == "buy":
                    institution["buy_amt"] += amt
                else:
                    institution["sell_amt"] += amt
    institution["buy_amt"] = round(institution["buy_amt"] / 10000, 1)
    institution["sell_amt"] = round(institution["sell_amt"] / 10000, 1)
    institution["net_amt"] = round(institution["buy_amt"] - institution["sell_amt"], 1)

    return {"records": records, "seats": seats, "institution": institution}


# ============================================================================
# 5. 全市场龙虎榜
# ============================================================================

def daily_dragon_tiger(trade_date: str = None, min_net_buy: float = None) -> dict:
    """
    全市场龙虎榜。
    trade_date: YYYY-MM-DD（默认当日）
    min_net_buy: 净买入下限（万元），None 不过滤
    返回: {date, total_records, stocks: [{code, name, reason, close, change_pct,
           net_buy_wan, buy_wan, sell_wan, turnover_pct}]}
    """
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    data = eastmoney_datacenter(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        filter_str=f"(TRADE_DATE>='{trade_date}')(TRADE_DATE<='{trade_date}')",
        page_size=500,
        sort_columns="BILLBOARD_NET_AMT", sort_types="-1",
    )
    if not data:
        return {"date": trade_date, "total_records": 0, "stocks": [],
                "note": "无数据（非交易日或盘后未更新）"}

    actual_date = str(data[0].get("TRADE_DATE", ""))[:10] if data else trade_date
    stocks = []
    for row in data:
        net_buy = (row.get("BILLBOARD_NET_AMT") or 0) / 10000
        if min_net_buy is not None and net_buy < min_net_buy:
            continue
        stocks.append({
            "code": row.get("SECURITY_CODE", ""),
            "name": row.get("SECURITY_NAME_ABBR", ""),
            "reason": row.get("EXPLANATION", ""),
            "close": row.get("CLOSE_PRICE") or 0,
            "change_pct": round(float(row.get("CHANGE_RATE") or 0), 2),
            "net_buy_wan": round(net_buy, 1),
            "buy_wan": round((row.get("BILLBOARD_BUY_AMT") or 0) / 10000, 1),
            "sell_wan": round((row.get("BILLBOARD_SELL_AMT") or 0) / 10000, 1),
            "turnover_pct": round(float(row.get("TURNOVERRATE") or 0), 2),
        })
    return {"date": actual_date, "total_records": len(stocks), "stocks": stocks}


# ============================================================================
# 6. 行业板块排名（东财 push2）
# ============================================================================

def industry_comparison(top_n: int = 20) -> dict:
    """
    全行业涨跌幅排名（东财行业板块，~100 个行业）。
    返回: {top: [...], bottom: [...], total: int}
    """
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "100", "po": "1", "np": "1",
        "fltt": "2", "invt": "2",
        "fs": "m:90+t:2",
        "fields": "f2,f3,f4,f12,f13,f14,f104,f105,f128,f136,f140,f141,f207",
    }
    headers = {"User-Agent": UA}
    r = em_get(url, params=params, headers=headers, timeout=15)
    d = r.json()
    items = d.get("data", {}).get("diff", [])
    if not items:
        return {"top": [], "bottom": [], "total": 0}

    rows = []
    for i, item in enumerate(items):
        rows.append({
            "rank": i + 1,
            "name": item.get("f14", ""),
            "change_pct": item.get("f3", 0),
            "code": item.get("f12", ""),
            "up_count": item.get("f104", 0),
            "down_count": item.get("f105", 0),
            "leader": item.get("f140", ""),
            "leader_change": item.get("f136", 0),
        })

    return {
        "top": rows[:top_n],
        "bottom": rows[-top_n:],
        "total": len(rows),
    }


# ============================================================================
# 7. ?????????????IP ? ???????2?
# ============================================================================

def tencent_quote(codes: list[str]) -> dict:
    """
    ???????????? ? PE/PB/??/???/???? ?????
    codes: ["688017", "300476", "002463"]
    ?????: ["000001", "000300", "399006"]
    ???ETF: ["510050", "510300"]
    ??: {code: {name, price, pe_ttm, pb, mcap_yi, float_mcap_yi, ...}}
    ??: GBK
    """
    prefixed = []
    for c in codes:
        if c.startswith(("6", "9")):
            prefixed.append(f"sh{c}")
        elif c.startswith("8"):
            prefixed.append(f"bj{c}")
        else:
            prefixed.append(f"sz{c}")

    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    r.encoding = "gbk"
    data = r.text

    result = {}
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        code = key[2:]
        result[code] = {
            "name":          vals[1],
            "price":         float(vals[3]) if vals[3] else 0,
            "last_close":    float(vals[4]) if vals[4] else 0,
            "open":          float(vals[5]) if vals[5] else 0,
            "change_amt":    float(vals[31]) if vals[31] else 0,
            "change_pct":    float(vals[32]) if vals[32] else 0,
            "high":          float(vals[33]) if vals[33] else 0,
            "low":           float(vals[34]) if vals[34] else 0,
            "amount_wan":    float(vals[37]) if vals[37] else 0,
            "turnover_pct":  float(vals[38]) if vals[38] else 0,
            "pe_ttm":        float(vals[39]) if vals[39] else 0,
            "amplitude_pct": float(vals[43]) if vals[43] else 0,
            "mcap_yi":       float(vals[44]) if vals[44] else 0,
            "float_mcap_yi": float(vals[45]) if vals[45] else 0,
            "pb":            float(vals[46]) if vals[46] else 0,
            "limit_up":      float(vals[47]) if vals[47] else 0,
            "limit_down":    float(vals[48]) if vals[48] else 0,
            "vol_ratio":     float(vals[49]) if vals[49] else 0,
            "pe_static":     float(vals[52]) if vals[52] else 0,
        }
    return result


# ============================================================================
# 8. ?????K???? MA5/MA10/MA20?
# ============================================================================

def baidu_kline_with_ma(code: str, start_time: str = "") -> dict:
    """
    ?????K? ? ????: ????? ma5/ma10/ma20 ???
    code: 6?????
    start_time: ??????=?????? "2026-01-01"
    ??: {keys: [...], rows: [...]}
    """
    url = "https://finance.pae.baidu.com/selfselect/getstockquotation"
    params = {
        "all": "1", "isIndex": "false", "isBk": "false", "isBlock": "false",
        "isFutures": "false", "isStock": "true", "newFormat": "1",
        "group": "quotation_kline_ab", "finClientType": "pc",
        "code": code, "start_time": start_time, "ktype": "1",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/vnd.finance-web.v1+json",
        "Origin": "https://gushitong.baidu.com",
        "Referer": "https://gushitong.baidu.com/",
    }
    r = requests.get(url, params=params, headers=headers, timeout=10)
    d = r.json()
    result = d.get("Result", {})
    md = result.get("newMarketData", {})
    keys = md.get("keys", [])
    rows = md.get("marketData", "").split(";")
    return {"keys": keys, "rows": rows}


# ============================================================================
# 9. ???????????????73ms?
# ============================================================================

def ths_hot_reason(date: str = None) -> list[dict]:
    """
    ?????????? ? ?????????? reason?
    date: "YYYY-MM-DD" ???None=??
    ??: [{name, code, reason, close, change_pct, turnover_pct, amount, volume, dde_net, market}, ...]
    ??: 73ms ?? ~125 ?
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    url = (
        f"http://zx.10jqka.com.cn/event/api/getharden/"
        f"date/{date}/orderby/date/orderway/desc/charset/GBK/"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "Chrome/117.0.0.0 Safari/537.36"
        )
    }
    r = requests.get(url, headers=headers, timeout=10)
    data = r.json()
    if data.get("errocode", 0) != 0:
        raise RuntimeError(f"???????: {data.get('errormsg', '')}")

    rows = data.get("data") or []
    result = []
    for row in rows:
        result.append({
            "name": row.get("name", ""),
            "code": row.get("code", ""),
            "reason": row.get("reason", ""),
            "close": row.get("close", ""),
            "change_pct": row.get("zhangfu", ""),
            "turnover_pct": row.get("huanshou", ""),
            "amount": row.get("chengjiaoe", ""),
            "volume": row.get("chengjiaoliang", ""),
            "dde_net": row.get("ddejingliang", ""),
            "market": row.get("market", ""),
        })
    return result


# ============================================================================
# 10. ??????????
# ============================================================================

_HSGT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "Chrome/117.0.0.0 Safari/537.36"
    ),
    "Host": "data.hexin.cn",
    "Referer": "https://data.hexin.cn/market/hsgt/",
}


def hsgt_realtime() -> dict:
    """
    ?????????????????? 09:10-15:00?262 ??????
    ??: {records: [{time, hgt_yi, sgt_yi}, ...], summary: {hgt_yi, sgt_yi, total_yi}}
    ??: ??
    """
    url = "https://data.hexin.cn/market/hsgtApi/method/dayChart/"
    r = requests.get(url, headers=_HSGT_HEADERS, timeout=10)
    d = r.json()
    times = d.get("time", [])
    hgt = d.get("hgt", [])
    sgt = d.get("sgt", [])

    n = len(times)
    records = []
    for i in range(n):
        records.append({
            "time": times[i],
            "hgt_yi": hgt[i] if i < len(hgt) else None,
            "sgt_yi": sgt[i] if i < len(sgt) else None,
        })

    hgt_total = hgt[-1] if hgt else 0
    sgt_total = sgt[-1] if sgt else 0

    return {
        "records": records,
        "summary": {
            "hgt_yi": hgt_total,
            "sgt_yi": sgt_total,
            "total_yi": (hgt_total or 0) + (sgt_total or 0),
        }
    }



# ============================================================================
# === 研报层 =================================================================
# ============================================================================

def eastmoney_reports(code: str, max_pages: int = 5) -> list[dict]:
    all_records = []
    for page in range(1, max_pages + 1):
        params = {
            'industryCode': '*', 'pageSize': '100', 'industry': '*',
            'rating': '*', 'ratingChange': '*',
            'beginTime': '2000-01-01', 'endTime': '2030-01-01',
            'pageNo': str(page), 'fields': '', 'qType': '0',
            'orgCode': '', 'code': code, 'rcode': '',
            'p': str(page), 'pageNum': str(page), 'pageNumber': str(page),
        }
        r = em_get(REPORT_API, params=params,
                   headers={'Referer': 'https://data.eastmoney.com/'}, timeout=30)
        d = r.json()
        rows = d.get('data') or []
        if not rows:
            break
        all_records.extend(rows)
        if page >= (d.get('TotalPage', 1) or 1):
            break
    return all_records


def download_pdf(record: dict, target_dir: str = './reports'):
    info_code = record.get('infoCode', '')
    if not info_code:
        return None
    date = (record.get('publishDate') or '')[:10]
    org = record.get('orgSName') or 'unknown'
    title = re.sub(r'[\\/:*?<>|]', '_', record.get('title', ''))[:80]
    fname = f'{date}_{org}_{title}.pdf'
    target = Path(target_dir) / fname
    if target.exists():
        return str(target)
    url = PDF_TPL.format(info_code=info_code)
    r = em_get(url, headers={'Referer': 'https://data.eastmoney.com/'}, timeout=60)
    if r.status_code == 200 and len(r.content) >= 1024:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(r.content)
        return str(target)
    return None


def ths_eps_forecast(code: str) -> list[dict]:
    if not HAS_PANDAS:
        raise ImportError('ths_eps_forecast requires pandas')
    url = f'https://basic.10jqka.com.cn/new/{code}/worth.html'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Referer': 'https://basic.10jqka.com.cn/',
    }
    r = requests.get(url, headers=headers, timeout=15)
    r.encoding = 'gbk'
    dfs = pd.read_html(StringIO(r.text))
    target_df = None
    for df in dfs:
        cols = [str(c) for c in df.columns]
        if any('EPS' in c or 'PE' in c for c in cols):
            target_df = df
            break
    if target_df is None and dfs:
        target_df = dfs[0]
    if target_df is None:
        return []
    rows = []
    for _, row in target_df.iterrows():
        vals = row.tolist()
        if len(vals) >= 4:
            rows.append({
                'year': str(vals[0]) if vals[0] is not None else '',
                'analysts': int(vals[1]) if pd.notna(vals[1]) else 0,
                'min_eps': float(vals[2]) if pd.notna(vals[2]) else 0,
                'mean_eps': float(vals[3]) if pd.notna(vals[3]) else 0,
                'max_eps': float(vals[4]) if len(vals) > 4 and pd.notna(vals[4]) else 0,
            })
    return rows


# ============================================================================
# === 新闻层 =================================================================
# ============================================================================

def eastmoney_stock_news(code: str, page_size: int = 20) -> list[dict]:
    cb = 'jQuery_news'
    url = 'https://search-api-web.eastmoney.com/search/jsonp'
    inner_params = json.dumps({
        'uid': '', 'keyword': code,
        'type': ['cmsArticleWebOld'],
        'client': 'web', 'clientType': 'web', 'clientVersion': 'curr',
        'param': {'cmsArticleWebOld': {'searchScope': 'default', 'sort': 'default',
                  'pageIndex': 1, 'pageSize': page_size, 'preTag': '', 'postTag': ''}},
    }, separators=(',', ':'))
    params = {'cb': cb, 'param': inner_params}
    headers = {'User-Agent': UA, 'Referer': 'https://so.eastmoney.com/'}
    r = em_get(url, params=params, headers=headers, timeout=15)
    text = r.text
    json_str = text[text.index('(') + 1 : text.rindex(')')]
    d = json.loads(json_str)
    rows = []
    articles = d.get('result', {}).get('cmsArticleWebOld', []) or []
    for a in articles:
        rows.append({
            'title': re.sub(r'<[^>]+>', '', a.get('title', '')),
            'content': re.sub(r'<[^>]+>', '', a.get('content', ''))[:200],
            'time': a.get('date', ''),
            'source': a.get('mediaName', ''),
            'url': a.get('url', ''),
        })
    return rows


def eastmoney_global_news(page_size: int = 50) -> list[dict]:
    url = 'https://np-weblist.eastmoney.com/comm/web/getFastNewsList'
    params = {
        'client': 'web', 'biz': 'web_724',
        'fastColumn': '102', 'sortEnd': '',
        'pageSize': str(page_size),
        'req_trace': str(uuid.uuid4()),
    }
    headers = {'User-Agent': UA, 'Referer': 'https://kuaixun.eastmoney.com/'}
    r = em_get(url, params=params, headers=headers, timeout=10)
    d = r.json()
    rows = []
    for item in d.get('data', {}).get('fastNewsList', []):
        rows.append({
            'title': item.get('title', ''),
            'summary': item.get('summary', '')[:200],
            'time': item.get('showTime', ''),
        })
    return rows




# ============================================================================
# === 资金面扩展 =============================================================
# ============================================================================

def margin_trading(code: str, page_size: int = 30) -> list[dict]:
    data = eastmoney_datacenter(
        'RPTA_WEB_RZRQ_GGMX',
        filter_str=f'(SCODE="{code}")',
        page_size=page_size,
        sort_columns='DATE', sort_types='-1',
    )
    rows = []
    for row in data:
        rows.append({
            'date': str(row.get('DATE', ''))[:10],
            'rzye': row.get('RZYE', 0),
            'rzmre': row.get('RZMRE', 0),
            'rzche': row.get('RZCHE', 0),
            'rqye': row.get('RQYE', 0),
            'rqmcl': row.get('RQMCL', 0),
            'rqchl': row.get('RQCHL', 0),
            'rzrqye': row.get('RZRQYE', 0),
        })
    return rows


def block_trade(code: str, page_size: int = 20) -> list[dict]:
    data = eastmoney_datacenter(
        'RPT_DATA_BLOCKTRADE',
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=page_size,
        sort_columns='TRADE_DATE', sort_types='-1',
    )
    rows = []
    for row in data:
        close = row.get('CLOSE_PRICE') or 0
        deal_price = row.get('DEAL_PRICE') or 0
        premium = ((deal_price / close - 1) * 100) if close else 0
        rows.append({
            'date': str(row.get('TRADE_DATE', ''))[:10],
            'price': deal_price,
            'close': close,
            'premium_pct': round(premium, 2),
            'vol': row.get('DEAL_VOLUME', 0),
            'amount': row.get('DEAL_AMT', 0),
            'buyer': row.get('BUYER_NAME', ''),
            'seller': row.get('SELLER_NAME', ''),
        })
    return rows


def lockup_expiry(code: str, trade_date: str, forward_days: int = 90) -> dict:
    history_data = eastmoney_datacenter(
        'RPT_LIFT_STAGE',
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=15,
        sort_columns='FREE_DATE', sort_types='-1',
    )
    history = []
    for row in history_data:
        history.append({
            'date': str(row.get('FREE_DATE', ''))[:10],
            'type': row.get('LIMITED_STOCK_TYPE', ''),
            'shares': row.get('FREE_SHARES_NUM', 0),
            'ratio': row.get('FREE_RATIO', 0),
        })
    end_date = datetime.strptime(trade_date, '%Y-%m-%d') + timedelta(days=forward_days)
    end_str = end_date.strftime('%Y-%m-%d')
    upcoming_data = eastmoney_datacenter(
        'RPT_LIFT_STAGE',
        filter_str=f'(SECURITY_CODE="{code}")(FREE_DATE>=\'{trade_date}\')(FREE_DATE<=\'{end_str}\')',
        page_size=20,
        sort_columns='FREE_DATE', sort_types='1',
    )
    upcoming = []
    for row in upcoming_data:
        upcoming.append({
            'date': str(row.get('FREE_DATE', ''))[:10],
            'type': row.get('LIMITED_STOCK_TYPE', ''),
            'shares': row.get('FREE_SHARES_NUM', 0),
            'ratio': row.get('FREE_RATIO', 0),
        })
    return {'history': history, 'upcoming': upcoming}


def holder_num_change(code: str, page_size: int = 20) -> list[dict]:
    data = eastmoney_datacenter(
        'RPT_F10_EQUITY_ORGANIZE',
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=page_size,
        sort_columns='END_DATE', sort_types='-1',
    )
    rows = []
    for row in data:
        rows.append({
            'date': str(row.get('END_DATE', ''))[:10],
            'holder_num': row.get('HOLDER_NUM', 0),
            'change_ratio': row.get('HOLDER_NUM_CHANGE', 0),
            'avg_shares': row.get('AVG_SHARES', 0),
        })
    return rows


def dividend_history(code: str, page_size: int = 20) -> list[dict]:
    data = eastmoney_datacenter(
        'RPT_SHAREBONUS_DET',
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=page_size,
        sort_columns='EX_DIVIDEND_DATE', sort_types='-1',
    )
    rows = []
    for row in data:
        rows.append({
            'date': str(row.get('EX_DIVIDEND_DATE', ''))[:10],
            'bonus_rmb': row.get('PRETAX_BONUS_RMB', 0),
            'transfer_ratio': row.get('TRANSFER_RATIO', 0),
            'bonus_ratio': row.get('BONUS_RATIO', 0),
            'plan': row.get('ASSIGN_PROGRESS', ''),
        })
    return rows




# ============================================================================
# === 基础数据层 =============================================================
# ============================================================================

def sina_financial_report(code: str, report_type: str = 'lrb', num: int = 8) -> list[dict]:
    prefix = 'sh' if code.startswith('6') else 'sz'
    paper_code = f'{prefix}{code}'
    url = 'https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022'
    params = {
        'paperCode': paper_code,
        'source': report_type,
        'type': '0',
        'page': '1',
        'num': str(num),
    }
    headers = {'User-Agent': UA}
    r = requests.get(url, params=params, headers=headers, timeout=15)
    report_list = r.json().get('result', {}).get('data', {}).get('report_list', {}) or {}
    rows = []
    for period in sorted(report_list.keys(), reverse=True)[:num]:
        obj = report_list[period]
        rec = {'report_date': f'{period[:4]}-{period[4:6]}-{period[6:8]}'}
        for it in obj.get('data', []) or []:
            title = it.get('item_title', '')
            if not title or it.get('item_value') is None:
                continue
            rec[title] = it.get('item_value')
            tongbi = it.get('item_tongbi')
            if tongbi is not None:
                rec[f'{title}_yoy'] = tongbi
        rows.append(rec)
    return rows


# ============================================================================
# === 公告层 =================================================================
# ============================================================================

def _cninfo_ts_to_date(ts):
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d')
    return str(ts)[:10] if ts else ''


def cninfo_announcements(code: str, page_size: int = 30) -> list[dict]:
    url = 'https://www.cninfo.com.cn/new/hisAnnouncement/query'
    if code.startswith('6'):
        org_id = f'gssh0{code}'
    elif code.startswith(('8', '4')):
        org_id = f'gsbj0{code}'
    else:
        org_id = f'gssz0{code}'
    payload = {
        'stock': f'{code},{org_id}',
        'tabName': 'fulltext',
        'pageSize': str(page_size),
        'pageNum': '1',
        'column': '', 'category': '', 'plate': '',
        'seDate': '', 'searchkey': '', 'secid': '',
        'sortName': '', 'sortType': '', 'isHLtitle': 'true',
    }
    headers = {
        'User-Agent': UA,
        'Content-Type': 'application/x-www-form-urlencoded',
        'Referer': 'https://www.cninfo.com.cn/new/disclosure',
        'Origin': 'https://www.cninfo.com.cn',
    }
    r = requests.post(url, data=payload, headers=headers, timeout=15)
    d = r.json()
    rows = []
    for item in d.get('announcements', []) or []:
        rows.append({
            'title': item.get('announcementTitle', ''),
            'type': item.get('announcementTypeName', ''),
            'date': _cninfo_ts_to_date(item.get('announcementTime', '')),
            'url': f'https://www.cninfo.com.cn/new/disclosure/detail?announcementId={item.get("announcementId","")}&announcementTime={item.get("announcementTime","")}',
        })
    return rows




# ============================================================================
# === 估值工具层 =============================================================
# ============================================================================

def forward_pe(price: float, eps: float) -> float:
    if eps <= 0:
        return float('inf')
    return price / eps


def calc_peg(pe: float, cagr: float) -> float:
    if cagr <= 0:
        return float('inf')
    return pe / (cagr * 100)


def pe_digestion(current_pe: float, cagr: float, target_pe: float = 30) -> float:
    if current_pe <= target_pe:
        return 0.0
    if cagr <= 0:
        return float('inf')
    return math.log(current_pe / target_pe) / math.log(1 + cagr)


def full_valuation(code: str) -> dict:
    if not HAS_PANDAS:
        raise ImportError('full_valuation requires pandas')
    prefix = 'sh' if code.startswith(('6', '9')) else ('bj' if code.startswith('8') else 'sz')
    url = f'https://qt.gtimg.cn/q={prefix}{code}'
    r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
    r.encoding = 'gbk'
    data = r.text
    vals = data.split('"')[1].split('~')
    price = float(vals[3])
    mcap = float(vals[44])
    pe_ttm = float(vals[39]) if vals[39] else 0
    pb = float(vals[46]) if vals[46] else 0
    name = vals[1]
    eps_cur = eps_next = None
    analyst_count = 0
    try:
        eps_rows = ths_eps_forecast(code)
        if eps_rows:
            if len(eps_rows) >= 1:
                eps_cur = eps_rows[0].get('mean_eps')
                analyst_count = eps_rows[0].get('analysts', 0)
            if len(eps_rows) >= 2:
                eps_next = eps_rows[1].get('mean_eps')
    except Exception:
        pass
    pe_fwd = price / eps_cur if eps_cur else float('inf')
    cagr = (eps_next / eps_cur - 1) if (eps_cur and eps_next) else 0
    peg = pe_fwd / (cagr * 100) if cagr > 0 else float('inf')
    digest = (
        math.log(pe_fwd / 30) / math.log(1 + cagr)
        if pe_fwd > 30 and cagr > 0 else 0
    )
    return {
        'name': name, 'price': price, 'mcap_yi': mcap,
        'pe_ttm': pe_ttm, 'pb': pb,
        'eps_cur': eps_cur, 'eps_next': eps_next,
        'pe_fwd': round(pe_fwd, 1) if eps_cur else None,
        'cagr_pct': round(cagr * 100, 0) if cagr else None,
        'peg': round(peg, 2) if peg != float('inf') else None,
        'digest_years': round(digest, 1),
        'analyst_count': analyst_count,
    }


# ============================================================================
# === iwencai 语义搜索 =======================================================
# ============================================================================

def _claw_headers(call_type: str = 'normal') -> dict:
    return {
        'X-Claw-Call-Type': call_type,
        'X-Claw-Skill-Id': 'report-search',
        'X-Claw-Skill-Version': '2.0.0',
        'X-Claw-Plugin-Id': 'none',
        'X-Claw-Plugin-Version': 'none',
        'X-Claw-Trace-Id': secrets.token_hex(32),
    }


def iwencai_search(query: str, channel: str = 'report', size: int = 50) -> list[dict]:
    if not IWENCAI_KEY:
        raise RuntimeError('IWENCAI_API_KEY not set. Apply: https://www.iwencai.com/skillhub')
    headers = {
        'Authorization': f'Bearer {IWENCAI_KEY}',
        'Content-Type': 'application/json',
        **_claw_headers(),
    }
    payload = {
        'channels': [channel],
        'app_id': 'AIME_SKILL',
        'query': query,
        'size': size,
    }
    r = requests.post(
        f'{IWENCAI_BASE}/v1/comprehensive/search',
        json=payload, headers=headers, timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f'iwencai HTTP {r.status_code}: {r.text[:200]}')
    data = r.json()
    if data.get('status_code', 0) != 0:
        raise RuntimeError(f'iwencai error: {data.get("status_msg", "")}')
    return data.get('data') or []


def iwencai_query(query: str, page: int = 1, limit: int = 50) -> list[dict]:
    if not IWENCAI_KEY:
        raise RuntimeError('IWENCAI_API_KEY not set. Apply: https://www.iwencai.com/skillhub')
    headers = {
        'Authorization': f'Bearer {IWENCAI_KEY}',
        'Content-Type': 'application/json',
        **_claw_headers(),
    }
    payload = {
        'query': query,
        'page': str(page),
        'limit': str(limit),
        'is_cache': '1',
        'expand_index': 'true',
    }
    r = requests.post(
        f'{IWENCAI_BASE}/v1/query2data',
        json=payload, headers=headers, timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f'iwencai HTTP {r.status_code}: {r.text[:200]}')
    data = r.json()
    if data.get('status_code', 0) != 0:
        raise RuntimeError(f'iwencai error: {data.get("status_msg", "")}')
    return data.get('datas') or []


def dedup_articles(articles: list[dict]) -> list[dict]:
    best = {}
    for a in articles:
        uid = a.get('uid', '') or f'{a.get("title","")}|{a.get("publish_date","")}'
        score = float(a.get('score', 0))
        if uid not in best or score > float(best[uid].get('score', 0)):
            best[uid] = a
    return sorted(best.values(), key=lambda x: x.get('publish_date', ''), reverse=True)

