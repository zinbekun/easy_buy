import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
import history
import scraper
import sheets
import storage

ITEM = """
<li class="item product product-item">
 <a href="https://x/{sku}.html" class="product photo product-item-photo">
  <img src="pixel.jpg" data-original="https://img/{sku}.jpg" class="product-image-photo"></a>
 <a class="product-item-link" href="https://x/{sku}.html">{name}</a>
 <p class="price-wrapper"><span class="floor-price">{base}</span>円</p>
 {tax}
 <form data-product-sku="{sku}"></form>{button}
</li>"""


def item(sku, name, base, tax="<span class=\"floor-tax\">{}</span><span class=\"decimal-tax\">.44</span>", button='<button class="action tocart">カゴ追加</button>'):
    whole = int(base * 1.08)
    return ITEM.format(sku=sku, name=name, base=base, tax=tax.format(whole), button=button)


def page(*items, next_page=False):
    nav = '<li class="item pages-item-next"><a href="#">次</a></li>' if next_page else ""
    return f"<ul>{nav}</ul><ol>{''.join(items)}</ol>"


def test_parse_listing_reads_fields():
    items, has_next = scraper.parse_listing(page(item("1", "カルピス 500ml", 108), next_page=True))
    assert has_next
    assert items[0]["id"] == "1"
    assert items[0]["price"] == 116.44
    assert items[0]["base_price"] == 108
    assert items[0]["image"] == "https://img/1.jpg"
    assert items[0]["content"] == "500ml"
    assert items[0]["sold_out"] is False


def test_parse_listing_flags_sold_out_and_unknown_price():
    items, _ = scraper.parse_listing(page(item("1", "a", 100, button=""), item("2", "b", 100, tax="")))
    assert items[0]["sold_out"] is True
    assert items[1]["price"] is None


@pytest.mark.parametrize("name,expected", [
    ("サントリー 伊右衛門 500ml×24", ("500ml×24", 12000, 0)),
    ("烏龍茶 2L", ("2L", 2000, 0)),
    ("そばつゆ 30g×3袋", ("30g×3", 90, 0)),
    ("野菜生活100 オリジナル 200ml", ("200ml", 200, 0)),
    ("チロルチョコ 1個入", ("1個", 1, 1)),
    ("スパゲッティ 1.7mm 500g", ("500g", 500, 0)),
    ("なぞの商品", ("", 0, 2)),
])
def test_parse_content(name, expected):
    assert scraper.parse_content(name) == expected


@pytest.mark.parametrize("name,expected", [
    ("日清食品 デカうま 豚キムチ 101g", True),
    ("トップバリュベストプライス 旨つゆ天そば 101g", True),
    ("トップバリュベストプライス そばつゆ 30g×3袋", False),
    ("シマダヤ 真打きしめん 200g", False),
    ("トップバリュベストプライス スパゲッティ 1.7mm 500g", False),
])
def test_is_cup_noodle(name, expected):
    assert scraper.is_cup_noodle(name) is expected


def fetch_all(session):
    """ブラウザの巡回を再現して全カテゴリーを取得する。"""
    target = scraper.Scraper(session=session, sleep=lambda s: None)
    products, step = {}, {"source": 0, "page": 1}
    while step:
        result = target.fetch_page(step["source"], step["page"])
        for item in result["items"]:
            products.setdefault(item["id"], item)
        step = result["next"]
    return list(products.values())


class FakeResponse:
    def __init__(self, text=None, error=None):
        self.text, self.error = text, error

    def raise_for_status(self):
        if self.error:
            raise self.error


class FakeSession:
    def __init__(self, responder):
        self.responder, self.urls = responder, []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return self.responder(url)


def test_fetch_all_filters_and_stops_at_price_limit():
    def responder(url):
        if "1M.html" in url and "p=1" in url:
            return FakeResponse(page(item("1", "安い菓子", 118), item("2", "同額", 119), next_page=True))
        if "1M.html" in url:
            return FakeResponse(page(item("3", "高い菓子", 128), next_page=True))
        if "1G.html" in url:
            return FakeResponse(page(item("4", "麺職人 87g", 118), item("5", "そばつゆ 30g", 98)))
        return FakeResponse(page(item("1", "重複", 118), item("6", "売切", 50, button="")))

    session = FakeSession(responder)
    products = fetch_all(session)
    assert {p["id"]: p["category"] for p in products}["1"] == "お菓子"
    assert "4" in {p["id"] for p in products} and "5" not in {p["id"] for p in products}
    assert "6" not in {p["id"] for p in products}
    assert "3" not in {p["id"] for p in products}
    assert sum("1M.html" in u for u in session.urls) == 2


def test_price_128_exact_is_excluded():
    html = page(item("1", "x", 118, tax='<span class="floor-tax">128</span><span class="decimal-tax">.00</span>'))
    session = FakeSession(lambda url: FakeResponse(html))
    products = fetch_all(session)
    assert products == []


def test_retries_three_times_then_fails():
    session = FakeSession(lambda url: FakeResponse(error=requests.HTTPError("503")))
    with pytest.raises(scraper.ScrapeError):
        fetch_all(session)
    assert len(session.urls) == 4


def test_retry_recovers_and_empty_page_counts_as_failure():
    calls = []

    def responder(url):
        calls.append(url)
        return FakeResponse("<html>メンテナンス中</html>") if len(calls) == 1 else FakeResponse(page(item("1", "a", 100)))

    products = fetch_all(FakeSession(responder))
    assert products


def product(i, category="お菓子", price=100.0, kana="あ", name=None):
    return {"id": str(i), "name": name or f"p{i}", "category": category, "url": f"https://x/{i}", "image": "",
            "content": "", "price": price, "kana": kana}


def rows(products):
    return [sheets.product_row(p) for p in products]


def grid(rows=6):
    data = [[""] * 8 for _ in range(rows)]
    data[1][1] = 45693
    data[1][4] = "総計"
    data[1][5] = "=SUM(G4:G)"
    data[2] = ["", "名称", "分類", "購入リンク", "価格（税込み）", "数量", "小計", "コメント"]
    data[3] = ["", "古い商品", "お菓子", "u", 100, 1, "=E4*F4", "メモ"]
    data[4] = ["", "古い商品2", "飲み物", "u", 90, 1, "=E5*F5", ""]
    return data


def test_build_updates_replaces_data_and_keeps_formulas():
    updates, last = sheets.build_updates(grid(), rows([product(1, price=116.44)]), 6, datetime(2026, 10, 31))
    assert updates[0] == {"range": "B2", "values": [["2026/10/31"]]}
    body = updates[1]
    assert body["range"] == "B4:H6"
    assert body["values"][0] == ["p1", "お菓子", None, 116.44, 1, "=E4*F4", ""]
    assert body["values"][1] == ["", "", None, "", "", "=E5*F5", ""]
    assert len(updates) == 2 and last == 6


def test_build_updates_adds_rows_formula_and_total():
    data = grid()
    data[1][5] = ""
    items = [product(i) for i in range(5)]
    updates, last = sheets.build_updates(data, rows(items), 6, datetime(2026, 10, 31))
    assert last == 8
    assert updates[1]["values"][4][5] == "=E8*F8"
    assert updates[2] == {"range": "F2", "values": [["=sum(G4:G)"]]}


def test_header_missing_is_error():
    with pytest.raises(sheets.SheetError):
        sheets.build_updates([["x"]], rows([product(1)]), 10, datetime(2026, 10, 31))


def test_link_request_writes_title_and_clears_old_rows():
    update = sheets.build_link_request(grid(), rows([product(1, name="コーラ 500ml")]), 6, 99)["updateCells"]
    assert update["range"] == {"sheetId": 99, "startRowIndex": 3, "endRowIndex": 6, "startColumnIndex": 3, "endColumnIndex": 4}
    assert "textFormat.link" in update["fields"]
    assert update["rows"][0]["values"][0] == {
        "userEnteredValue": {"stringValue": "おうちでイオン　イオンネットスーパー　コーラ 500ml"},
        "userEnteredFormat": {"textFormat": {"link": {"uri": "https://x/1"}}},
    }
    assert update["rows"][1]["values"][0] == {}
    assert len(update["rows"]) == 3


class FakeWorksheet:
    title, index, id, row_count = "11月", 2, 7, 1000

    def __init__(self, grid):
        self.grid, self.updates = grid, None

    def get_values(self, *args, **kwargs):
        return self.grid

    def batch_update(self, updates, raw):
        self.updates = updates


class FakeSpreadsheet:
    id = "dest"

    def __init__(self, worksheet, after_copy):
        self.current, self.after_copy, self.requests = worksheet, after_copy, []

    def worksheet(self, title):
        return self.current

    def batch_update(self, body):
        self.requests += body["requests"]
        if "deleteSheet" in body["requests"][0]:
            self.current = self.after_copy


def test_blank_sheet_is_replaced_with_template(monkeypatch):
    class Template:
        copied_to = None

        def copy_to(self, spreadsheet_id):
            Template.copied_to = spreadsheet_id
            return {"sheetId": 55}

    class Source:
        def get_worksheet_by_id(self, sheet_id):
            assert sheet_id == 357456141
            return Template()

    monkeypatch.setattr(sheets, "TEMPLATE_SPREADSHEET_ID", "template")
    monkeypatch.setattr(sheets, "TEMPLATE_SHEET_ID", 357456141)
    monkeypatch.setattr(sheets, "open_spreadsheet", lambda key: Source())
    copied = FakeWorksheet(grid())
    spreadsheet = FakeSpreadsheet(FakeWorksheet([[], ["", " "]]), copied)
    sheets.transcribe(spreadsheet, "11月", [product(1)])
    assert Template.copied_to == "dest"
    delete, rename = spreadsheet.requests[:2]
    assert delete == {"deleteSheet": {"sheetId": 7}}
    assert rename["updateSheetProperties"]["properties"] == {"sheetId": 55, "title": "11月", "index": 2}
    assert copied.updates[1]["values"][0][0] == "p1"


def test_sheet_with_content_is_never_replaced(monkeypatch):
    monkeypatch.setattr(sheets, "open_spreadsheet", lambda key: pytest.fail("テンプレートを開いてはいけない"))
    worksheet = FakeWorksheet([["メモ"]])
    with pytest.raises(sheets.SheetError):
        sheets.transcribe(FakeSpreadsheet(worksheet, None), "11月", [product(1)])


def test_name_is_not_interpreted_as_formula():
    updates, _ = sheets.build_updates(grid(), rows([product(1, name="=IMPORTXML()")]), 6, datetime(2026, 10, 31))
    assert updates[1]["values"][0][0] == "'=IMPORTXML()"


def test_transcribe_order():
    items = [
        product(1, "飲み物", 10, "あ"),
        product(2, "カップ麺", 50, "あ"),
        product(3, "お菓子", 90, "か"),
        product(4, "お菓子", 90, "あ"),
        product(5, "お菓子", 20, "ん"),
    ]
    assert [p["id"] for p in sheets.transcribe_order(items)] == ["5", "4", "3", "2", "1"]


def test_build_updates_writes_quantity():
    updates, _ = sheets.build_updates(grid(), rows([{**product(1), "quantity": 7}]), 6, datetime(2026, 10, 31))
    assert updates[1]["values"][0][4] == 7


def test_read_quantities(monkeypatch):
    data = grid()
    data[3][5] = 6
    data[4][5] = ""
    data.append(["", "個数不明", "お菓子", "u", 10, "未定", "", ""])

    class Book:
        def worksheet(self, name):
            return FakeWorksheet(data)

    monkeypatch.setattr(sheets, "open_spreadsheet", lambda url: Book())
    assert sheets.read_quantities("url", "tab") == {"古い商品": 6}


def test_rakuchin_plan_keeps_only_same_price_on_sale():
    entry = {"items": [
        {**product(1, price=100.0), "name": "同じ", "quantity": 2},
        {**product(2, price=100.0), "name": "値上げ", "quantity": 3},
        {**product(3, price=100.0), "name": "販売終了", "quantity": 4},
    ]}
    plan = history.rakuchin_plan(entry, {"1": product(1, price=100.0), "2": product(2, price=110.0)}, {"同じ": 9})
    assert plan["kept_ids"] == ["1"]
    assert [item["quantity"] for item in plan["previous"]] == [9, 3, 4]


def test_history_keeps_five_and_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    for n in range(6):
        history.add([product(1), product(n + 10)])
    entries = history.load()
    assert len(entries) == 5
    assert all(item["id"] != "10" for e in entries for item in e["items"])
    assert history.previous_ids() == ["1", "15"]
    items = {m["id"]: m for m in history.summary({"1"})}
    assert items["1"]["count"] == 5 and items["1"]["on_sale"]
    assert items["11"]["count"] == 1 and not items["11"]["on_sale"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app as server

    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    return server, server.app.test_client()


class FakeScraper:
    fail = False

    def fetch_page(self, source, page):
        if FakeScraper.fail and source == 1:
            raise scraper.ScrapeError("失敗")
        following = {"source": source + 1, "page": 1} if source < 2 else None
        return {"items": [{"id": str(source), "name": "x"}], "unknown": 1, "progress": 0.5, "next": following}


def crawl(http):
    responses, step = [], {"source": 0, "page": 1}
    while step:
        res = http.post("/api/crawl", json={"run_id": "r1", **step})
        responses.append(res)
        if res.status_code != 200:
            break
        step = res.get_json()["next"]
    return responses


def test_crawl_replaces_products_only_after_last_page(client, monkeypatch):
    server, http = client
    monkeypatch.setattr(server, "Scraper", FakeScraper)
    FakeScraper.fail = False
    assert http.get("/api/products").get_json()["stale"] is True
    crawl(http)
    data = http.get("/api/products").get_json()
    assert [p["id"] for p in data["products"]] == ["0", "1", "2"]
    assert data["price_unknown"] == 3 and data["stale"] is False


def test_failed_crawl_keeps_previous_products(client, monkeypatch):
    server, http = client
    monkeypatch.setattr(server, "Scraper", FakeScraper)
    storage.save("products", {"items": [{"id": "old"}], "fetched_at": "2026-01-01 00:00", "price_unknown": 0})
    FakeScraper.fail = True
    assert crawl(http)[-1].status_code == 502
    assert [p["id"] for p in http.get("/api/products").get_json()["products"]] == ["old"]
    assert list(storage.DATA_DIR.glob("draft-*")) == []


def test_password_protects_everything(client, monkeypatch):
    server, http = client
    monkeypatch.setattr(server, "APP_PASSWORD", "secret")
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    assert http.get("/api/products").status_code == 401
    assert http.post("/api/transcribe", json={}).status_code == 401
    assert http.get("/").status_code == 302
    assert http.post("/login", data={"password": "wrong"}).status_code == 401
    assert http.post("/login", data={"password": "secret"}).status_code == 302
    assert http.get("/api/products").status_code == 200


def test_redis_backend_sends_expected_commands(monkeypatch):
    sent = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": '{"a": 1}'}

    def post(url, json, headers, timeout):
        sent.append(json)
        assert headers["Authorization"] == "Bearer tok"
        return Response()

    monkeypatch.setattr(storage, "REDIS_URL", "https://kv")
    monkeypatch.setattr(storage, "REDIS_TOKEN", "tok")
    monkeypatch.setattr(storage.requests, "post", post)
    storage.save("draft", {"a": 1}, ttl_sec=60)
    assert storage.load("draft") == {"a": 1}
    storage.delete("draft")
    assert [c[0] for c in sent] == ["SET", "GET", "DEL"]
    assert sent[0][-2:] == ["EX", 60] and sent[0][1] == "aeon-kaidashi:draft"




@pytest.mark.parametrize("name,expected", [
    ("スコッティ ティシュー フラワーボックス 5箱", "ティッシュ"),
    ("トップバリュ 除菌ウエットティッシュ アルコール 100枚", "ウェットティッシュ"),
    ("トップバリュ 紙コップ 205ml 50個入", "紙コップ"),
    ("竹割り箸 100膳", "割りばし"),
    ("トイレットペーパー 12ロール", None),
])
def test_daily_group(name, expected):
    assert scraper.daily_group(name) == expected


def test_daily_goods_have_no_price_limit_and_read_every_page():
    def responder(url):
        if "8A.html" in url and "p=1" in url:
            return FakeResponse(page(item("1", "ボックスティッシュ 5箱", 598), item("2", "生理用ナプキン", 300),
                                     next_page=True))
        if "8A.html" in url:
            return FakeResponse(page(item("3", "ウェットティッシュ 詰替", 1200)))
        if "8F.html" in url:
            return FakeResponse(page(item("4", "紙コップ 50個", 980)))
        return FakeResponse(page(item("9", "安い菓子", 50)))

    session = FakeSession(responder)
    products = {p["id"]: p for p in fetch_all(session)}
    assert {i: (p["category"], p.get("group")) for i, p in products.items() if i != "9"} == {
        "1": ("日用品", "ティッシュ"), "3": ("日用品", "ウェットティッシュ"), "4": ("日用品", "紙コップ")}
    assert sum("8A.html" in u for u in session.urls) == 2


def sheet_with_genres():
    data = grid()
    data[3] = ["", "古い菓子", "お菓子", "おうちでイオン　古い菓子", 100, 3, "=E4*F4", "メモ"]
    data[4] = ["", "古いお茶", "飲み物", "おうちでイオン　古いお茶", 90, 2, "=E5*F5", ""]
    data.append(["", "コピー用紙", "製本用", "手入力のリンク", 500, 1, "=E7*F7", "研究室用"])
    return data


def test_kept_rows_keep_other_genres_as_written():
    kept = sheets.kept_rows(sheet_with_genres(), {5: "https://x/tea"}, {"お菓子"})
    assert [(r["name"], r["category"], r["link_uri"], r["quantity"], r["comment"]) for r in kept] == [
        ("古いお茶", "飲み物", "https://x/tea", 2, ""), ("コピー用紙", "製本用", "", 1, "研究室用")]


def test_merge_rows_orders_by_genre():
    kept = sheets.kept_rows(sheet_with_genres(), {}, {"お菓子"})
    merged = sheets.merge_rows(kept, rows([product(1, "お菓子", name="新しい菓子"), product(2, "日用品", name="ティッシュ")]))
    assert [r["category"] for r in merged] == ["お菓子", "飲み物", "日用品", "製本用"]


class CoopSpreadsheet:
    def __init__(self, data):
        self.ws = FakeWorksheet(data)
        self.ws.title, self.ws.row_count = "11月", 10
        self.requests = []

    def worksheet(self, name):
        return self.ws

    def fetch_sheet_metadata(self, params):
        assert params["ranges"] == "'11月'!D4:D7"
        return {"sheets": [{"data": [{"rowData": [{"values": [{"hyperlink": "https://x/old"}]},
                                                  {"values": [{"hyperlink": "https://x/tea"}]}]}]}]}

    def batch_update(self, body):
        self.requests += body["requests"]


@pytest.mark.parametrize("cooperative", [True, False])
def test_transcribe_cooperative_mode(cooperative):
    book = CoopSpreadsheet(sheet_with_genres())
    sheets.transcribe(book, "11月", [{**product(1, "お菓子", 50.0, name="新しい菓子"), "quantity": 4}], cooperative)
    written = [r for r in book.ws.updates[1]["values"] if r[0]]
    links = [c["values"][0] for c in book.requests[0]["updateCells"]["rows"] if c["values"][0]]
    if cooperative:
        assert [(r[0], r[1], r[3], r[4], r[6]) for r in written] == [
            ("新しい菓子", "お菓子", 50.0, 4, ""), ("古いお茶", "飲み物", 90, 2, ""), ("コピー用紙", "製本用", 500, 1, "研究室用")]
        assert [r[5] for r in written] == ["=E4*F4", "=E5*F5", "=E6*F6"]
        assert links[1] == {"userEnteredValue": {"stringValue": "おうちでイオン　古いお茶"},
                            "userEnteredFormat": {"textFormat": {"link": {"uri": "https://x/tea"}}}}
        assert links[2] == {"userEnteredValue": {"stringValue": "手入力のリンク"}}
    else:
        assert [r[0] for r in written] == ["新しい菓子"]


def test_cooperative_flag_reaches_sheet(client, monkeypatch):
    server, http = client
    storage.save("products", {"items": [product(1, price=50.0)], "fetched_at": "2026-11-09 10:00", "price_unknown": 0})
    storage.save("settings", {"spreadsheet_url": "https://docs.google.com/spreadsheets/d/abcdefghijklmnopqrstuvwxyz/edit",
                              "sheet_name": ""})
    calls = []
    monkeypatch.setattr(sheets, "open_spreadsheet", lambda url: object())
    monkeypatch.setattr(sheets, "transcribe", lambda book, name, items, cooperative: calls.append(cooperative))
    http.post("/api/transcribe", json={"ids": ["1"], "sheet_name": "11月", "cooperative": True})
    http.post("/api/transcribe", json={"ids": ["1"], "sheet_name": "11月"})
    assert calls == [True, False]


def test_blank_sheet_without_template_is_error(monkeypatch):
    monkeypatch.setattr(sheets, "TEMPLATE_SPREADSHEET_ID", "")
    with pytest.raises(sheets.SheetError):
        sheets.transcribe(FakeSpreadsheet(FakeWorksheet([]), None), "11月", [product(1)])
