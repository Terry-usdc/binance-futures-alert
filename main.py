import re
import json
import requests
from urllib.parse import quote
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

CATALOG_API = "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query"
DETAIL_API  = "https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.binance.com/zh-TC/support/announcement/list/48",
    "clienttype": "web",
}

def build_link(code: str, locale="zh-TC"):
    return f"https://www.binance.com/{locale}/support/announcement/detail/{quote(code)}" if code else None

def warmup_session(s: requests.Session):
    for u in [
        "https://www.binance.com/",
        "https://www.binance.com/zh-TC",
        "https://www.binance.com/zh-TC/support/announcement/list/48",
    ]:
        try:
            s.get(u, headers=HEADERS, timeout=15)
        except Exception:
            pass

def fetch_catalog(s: requests.Session, catalog_id=48, page_no=1, page_size=20):
    r = s.get(
        CATALOG_API,
        params={"catalogId": catalog_id, "pageNo": page_no, "pageSize": page_size},
        headers=HEADERS, timeout=15
    )
    r.raise_for_status()
    return r.json()["data"]["articles"]

def fetch_detail_data(s: requests.Session, code: str):
    r = s.get(DETAIL_API, params={"articleCode": code}, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()["data"]

# ---- 把 contentJson 的所有文字「按順序」吐成 lines ----
def walk_text(obj, out):
    if isinstance(obj, dict):
        if isinstance(obj.get("content"), str):
            out.append(obj["content"])
        for v in obj.values():
            walk_text(v, out)
    elif isinstance(obj, list):
        for v in obj:
            walk_text(v, out)

def extract_lines_from_content_json(content_json_str: str):
    j = json.loads(content_json_str)
    parts = []
    walk_text(j, parts)
    lines = []
    for p in parts:
        p = re.sub(r"\s+", " ", p).strip()
        if p:
            lines.append(p)
    return lines

# ---- 狀態機解析：時間行 -> 後面 USDT 行配對 ----
TIME_LINE_RE = re.compile(r"^(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*\(UTC\)\s*:?\s*$", re.IGNORECASE)
PAIR_RE = re.compile(r"\b([A-Z0-9]{2,15}USDT)\b")

def utc_to_taipei(date_str: str, time_str: str) -> str:
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    tw = dt.astimezone(ZoneInfo("Asia/Taipei"))
    return tw.strftime("%Y-%m-%d %H:%M (Asia/Taipei)")

def parse_pair_times_from_lines(lines, max_follow_lines=10):
    results = []
    for i, ln in enumerate(lines):
        m = TIME_LINE_RE.match(ln)
        if not m:
            continue

        d, t = m.group(1), m.group(2)
        utc_str = f"{d} {t} UTC"
        tw_str = utc_to_taipei(d, t)

        # 往後掃幾行找幣對（避免 footer/related 誤配）
        for j in range(i + 1, min(len(lines), i + 1 + max_follow_lines)):
            pairs = [p.upper() for p in PAIR_RE.findall(lines[j])]
            if pairs:
                for pair in pairs:
                    results.append({"pair": pair, "utc": utc_str, "taipei": tw_str})
                break

    # 去重：同幣對同時間
    uniq = {}
    for r in results:
        uniq[(r["pair"], r["utc"])] = r
    return list(uniq.values())

# ====== 主程式：抓「最近 5 篇」符合 Futures+Launch 的文章並解析 ======
s = requests.Session()
warmup_session(s)

articles = fetch_catalog(s, 48, 1, 20)

matched = []
for it in articles:
    title = it.get("title", "") or ""
    code = it.get("code")
    if code and ("Futures" in title) and ("Launch" in title):
        matched.append(it)
    if len(matched) >= 3:
        break

if not matched:
    print("最新 20 篇內找不到符合 Futures+Launch 的公告")
else:
    for idx, it in enumerate(matched, 1):
        title = it.get("title", "")
        code = it.get("code")
        print("\n==============================")
        print(f"[{idx}] 標題：", title)
        print("連結：", build_link(code))

        try:
            data = fetch_detail_data(s, code)
            content_json = data.get("contentJson")
            if not content_json:
                print("❌ 這篇沒有 contentJson，略過")
                continue

            lines = extract_lines_from_content_json(content_json)
            rows = parse_pair_times_from_lines(lines, max_follow_lines=10)

            if not rows:
                print("❌ 沒解析到『時間 -> 幣對』")
            else:
                for r in sorted(rows, key=lambda x: (x["utc"], x["pair"])):
                    print(f"- {r['pair']} | {r['utc']} | {r['taipei']}")
        except Exception as e:
            print("❌ 解析失敗：", e)
