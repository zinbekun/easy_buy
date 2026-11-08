"""イオンネットスーパーの一覧ページから商品情報を取得する。

一覧は税抜価格の安い順で巡回し、税抜でも上限以上になった時点で打ち切る。
税込みは税抜以上なので、それ以降に対象商品は現れない。
robots.txt が /netsuper/*/*/ を禁止しているため、1G/1G05.html のような
2階層のカテゴリーURLは使わず、1階層のカテゴリーページだけを巡回する。
"""
import math
import re
import time
import unicodedata
from dataclasses import dataclass

import pykakasi
import requests
from bs4 import BeautifulSoup

from config import (
    BASE_URL,
    DAILY_CATEGORY,
    MAX_PAGES_PER_CATEGORY,
    MAX_RETRIES,
    PRICE_LIMIT,
    REQUEST_TIMEOUT_SEC,
    RETRY_BACKOFF_SEC,
    USER_AGENT,
    USE_CUP_NOODLE_SUBCATEGORY,
)

SOLD_OUT_WORDS = ("売り切れ", "売切", "品切", "在庫切", "在庫なし", "販売終了")

# 1G.html は麺類全体で商品ごとの分類情報がないため、商品名で除外判定する。
# 見逃しは画面で気付けないので、明らかに別物の商品だけを除く。袋麺は混ざることがある。
NOT_CUP_NOODLE = re.compile(
    r"ラーメンスープ|らぁ麺スープ|そばつゆ|うどんつゆ|あげ玉|生ラーメン|生パスタ|パスタ|スパゲ|マカロニ"
    r"|フジッリ|ペンネ|冷麺|\(袋\)|単品での販売|シマダヤ|鍋の〆|1P|蒸し|メンマ|山菜"
    r"|ナポリタン|ミートソース|カルボナーラ|たらこクリーム|\d+袋|\d+食"
)

_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|ml|l)(?![a-z])(?:\s*[×x*]\s*(\d+))?", re.I)
_COUNT = re.compile(r"(\d+)\s*(個|本|枚|粒|袋|食|切れ|切|包|缶|パック)")
_TOTAL = re.compile(r"全([\d,]+)件")
_kakasi = pykakasi.kakasi()


class ScrapeError(Exception):
    pass


# 日用品は値段の上限なく、この4種類だけを商品名で選ぶ。ウェットティッシュを先に判定する
DAILY_GROUPS = (
    ("ウェットティッシュ", re.compile(r"ウェット|ウエット")),
    ("ティッシュ", re.compile(r"ティッシュ|ティシュ")),
    ("紙コップ", re.compile(r"紙コップ|ペーパーカップ")),
    ("割りばし", re.compile(r"割りばし|割り箸|割箸|わりばし")),
)


@dataclass
class Source:
    category: str
    path: str
    accepts: object = None
    price_limited: bool = True


def is_cup_noodle(name):
    text = unicodedata.normalize("NFKC", name)
    return not NOT_CUP_NOODLE.search(text)


def daily_group(name):
    text = unicodedata.normalize("NFKC", name)
    return next((group for group, pattern in DAILY_GROUPS if pattern.search(text)), None)


# 日用品の小分類ページは robots.txt の禁止範囲なので、それを含む売場を全ページ読む。
# ティッシュ類は「紙・生理用品・介護」（8A）、紙コップと割りばしは「キッチン用品」（8F）にある
SOURCES = [
    Source("お菓子", "1M.html"),
    Source("カップ麺", "1G/1G05.html")
    if USE_CUP_NOODLE_SUBCATEGORY
    else Source("カップ麺", "1G.html", is_cup_noodle),
    Source("飲み物", "1R.html"),
    Source(DAILY_CATEGORY, "8A.html", daily_group, price_limited=False),
    Source(DAILY_CATEGORY, "8F.html", daily_group, price_limited=False),
]


def parse_content(name):
    """商品名から内容量を読み取り (表示文字列, 比較値, 種別) を返す。

    種別 0 は g/ml、1 は個数、2 は不明。
    """
    text = unicodedata.normalize("NFKC", name)
    matches = list(_AMOUNT.finditer(text))
    if matches:
        m = matches[-1]
        value = float(m.group(1))
        if m.group(2).lower() in ("kg", "l"):
            value *= 1000
        if m.group(3):
            value *= int(m.group(3))
        return m.group(0), value, 0
    matches = list(_COUNT.finditer(text))
    if matches:
        m = matches[-1]
        return m.group(0), float(m.group(1)), 1
    return "", 0.0, 2


def kana_key(name):
    text = unicodedata.normalize("NFKC", name)
    return "".join(part["hira"] for part in _kakasi.convert(text))


def _number(text):
    return float(text.replace(",", ""))


def parse_listing(html):
    """一覧ページを解析し (商品のリスト, 次ページの有無) を返す。"""
    soup = BeautifulSoup(html, "lxml")
    items = []
    for li in soup.select("li.product-item"):
        link = li.select_one("a.product-item-link")
        if link is None:
            continue
        form = li.select_one("form[data-product-sku]")
        url = link.get("href", "").strip()
        name = link.get_text(strip=True)
        image = li.select_one("img.product-image-photo")

        base = li.select_one(".floor-price")
        tax = li.select_one(".floor-tax")
        decimal = li.select_one(".decimal-tax")
        base_price = _number(base.get_text(strip=True)) if base else None
        price = None
        if tax:
            price = _number(tax.get_text(strip=True) + (decimal.get_text(strip=True) if decimal else ""))

        body = li.get_text(" ", strip=True)
        content, content_value, content_group = parse_content(name)
        items.append({
            "id": form["data-product-sku"] if form else url,
            "name": name,
            "url": url,
            "image": image.get("data-original") or image.get("src", "") if image else "",
            "base_price": base_price,
            "price": price,
            "content": content,
            "content_value": content_value,
            "content_group": content_group,
            "sold_out": li.select_one("button.tocart") is None
            or any(word in body for word in SOLD_OUT_WORDS),
        })
    return items, soup.select_one(".pages-item-next a") is not None


class Scraper:
    def __init__(self, session=None, sleep=time.sleep):
        self.session = session or requests.Session()
        self.sleep = sleep

    def _retry(self, url, read):
        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            if attempt:
                self.sleep(RETRY_BACKOFF_SEC * attempt)
            try:
                res = self.session.get(
                    url,
                    timeout=REQUEST_TIMEOUT_SEC,
                    headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
                )
                res.raise_for_status()
                return read(res.text)
            except (requests.RequestException, ScrapeError) as e:
                last_error = e
        raise ScrapeError(f"{url} の取得に{MAX_RETRIES}回再試行しても失敗しました: {last_error}")

    def listing(self, url, allow_empty=False):
        """一覧ページを取得し (商品, 次ページの有無, ページ数) を返す。"""
        def read(text):
            items, has_next = parse_listing(text)
            if not items and not allow_empty:
                raise ScrapeError("商品を1件も読み取れませんでした（メンテナンス中、またはページ構造の変更の可能性）")
            total = _TOTAL.search(text)
            pages = math.ceil(int(total.group(1).replace(",", "")) / 100) if total else 1
            return items, has_next, max(pages, 1)

        return self._retry(url, read)

    def fetch_page(self, index, page):
        """1ページだけ取得し、次に取得する位置と進捗を返す。

        ブラウザが間隔を空けて順に呼ぶ。サーバーは呼び出しの間に状態を持たない。
        """
        source = SOURCES[index]
        items, has_next, pages = self.listing(f"{BASE_URL}/{source.path}?p={page}&sort=price_inc")
        prices = [i["base_price"] for i in items if i["base_price"] is not None]
        last = prices[-1] if prices else 0
        done_part = min(last / PRICE_LIMIT, 1.0) if source.price_limited else page / pages
        progress = (index + done_part) / len(SOURCES)

        accepted, unknown = [], 0
        for item in items:
            if source.accepts and not source.accepts(item["name"]):
                continue
            if item["price"] is None:
                unknown += 1
            elif (item["price"] < PRICE_LIMIT or not source.price_limited) and not item["sold_out"]:
                extra = {"group": daily_group(item["name"])} if source.category == DAILY_CATEGORY else {}
                accepted.append({**item, **extra, "category": source.category, "kana": kana_key(item["name"])})

        price_done = source.price_limited and last >= PRICE_LIMIT
        finished = not has_next or price_done or page >= MAX_PAGES_PER_CATEGORY
        if not finished:
            following = {"source": index, "page": page + 1}
        elif index + 1 < len(SOURCES):
            following = {"source": index + 1, "page": 1}
        else:
            following = None
        return {"items": accepted, "unknown": unknown, "progress": progress, "next": following}
