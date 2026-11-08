"""Googleスプレッドシートへの転記。"""
import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from config import CATEGORIES, SERVICE_ACCOUNT_PATH, TEMPLATE_SHEET_ID, TEMPLATE_SPREADSHEET_ID

JST = ZoneInfo("Asia/Tokyo")
HEADER_NAME = "名称"
HEADER_PRICE = "価格"
# 購入リンクは商品ページのタイトルを表示し、セルにリンクを付ける
LINK_TITLE_PREFIX = "おうちでイオン　イオンネットスーパー　"
LINK_COLUMN = 3


class SheetError(Exception):
    pass


def category_rank(category):
    return CATEGORIES.index(category) if category in CATEGORIES else len(CATEGORIES)


def transcribe_order(products):
    return sorted(products, key=lambda p: (category_rank(p["category"]), p["price"], p["kana"]))


def product_row(p):
    """商品を、シートの1行分の内容に変える。"""
    return {
        "name": _text(p["name"]),
        "category": p["category"],
        "link_text": LINK_TITLE_PREFIX + p["name"],
        "link_uri": p["url"],
        "price": p["price"],
        "quantity": p.get("quantity", 1),
        "comment": "",
    }


def kept_rows(grid, links, replaced):
    """協力モードで残す行を返す。今回出力しないジャンルの行を、書かれているまま拾う。

    links は行番号から購入リンクのURLへの対応。
    """
    rows = []
    for row in range(find_data_start(grid), len(grid) + 1):
        name, category = _cell(grid, row, 1), _cell(grid, row, 2)
        if (str(name).strip() == "" and str(category).strip() == "") or category in replaced:
            continue
        rows.append({
            "name": name,
            "category": category,
            "link_text": str(_cell(grid, row, LINK_COLUMN)),
            "link_uri": links.get(row, ""),
            "price": _cell(grid, row, 4),
            "quantity": _cell(grid, row, 5),
            "comment": _cell(grid, row, 7),
        })
    return rows


def merge_rows(kept, new):
    """残す行と新しい行を、ジャンルの順にまとめる。同じジャンル内の並びは変えない。"""
    return sorted(kept + new, key=lambda r: category_rank(r["category"]))


def _is_formula(value):
    return isinstance(value, str) and value.startswith("=")


def _text(value):
    # 文字列が数式として解釈されないようにする
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


def _cell(grid, row, col):
    """row は1始まり、col は 0=A列 の位置で読む。"""
    cells = grid[row - 1] if row - 1 < len(grid) else []
    return cells[col] if col < len(cells) else ""


def is_blank(grid):
    return not any(str(cell).strip() for row in grid for cell in row)


def find_data_start(grid):
    for row, cells in enumerate(grid, start=1):
        if str(_cell(grid, row, 1)).strip() == HEADER_NAME:
            if HEADER_PRICE not in str(_cell(grid, row, 4)):
                break
            return row + 1
    raise SheetError("見出し行が見つかりません。B列に「名称」、E列に「価格（税込み）」がある行が必要です（完全に空のシートならテンプレートをコピーします）。")


def build_updates(grid, rows, row_count, today):
    """シート全体の内容（数式表示）から batch_update の内容を組み立てる。

    (更新リスト, 必要な行数) を返す。数式のセルはそのまま書き戻して維持する。
    D列はリンク付きで書くため build_link_request に任せ、ここでは None で飛ばす。
    """
    start = find_data_start(grid)
    needed = start + len(rows) - 1
    last = max(row_count, needed)
    values = []
    for row in range(start, last + 1):
        index = row - start
        if index < len(rows):
            r = rows[index]
            new = [r["name"], r["category"], None, r["price"], r["quantity"], f"=E{row}*F{row}", r["comment"]]
        else:
            new = ["", "", None, "", "", "", ""]
        current = [_cell(grid, row, col) for col in range(1, 8)]
        values.append([old if _is_formula(old) else value for old, value in zip(current, new)])

    total = _cell(grid, 2, 5)
    updates = [
        {"range": "B2", "values": [[today.strftime("%Y/%m/%d")]]},
        {"range": f"B{start}:H{last}", "values": values},
    ]
    if not _is_formula(total):
        updates.append({"range": "F2", "values": [[f"=sum(G{start}:G)"]]})
    return updates, last


def build_link_request(grid, rows, last, sheet_id):
    """D列の表示文字とセルのリンクを書き換える updateCells リクエストを返す。"""
    start = find_data_start(grid)
    cells = []
    for row in range(start, last + 1):
        index = row - start
        current = _cell(grid, row, LINK_COLUMN)
        if _is_formula(current):
            cell = {"userEnteredValue": {"formulaValue": current}}
        elif index < len(rows):
            r = rows[index]
            cell = {"userEnteredValue": {"stringValue": r["link_text"]}}
            if r["link_uri"]:
                cell["userEnteredFormat"] = {"textFormat": {"link": {"uri": r["link_uri"]}}}
        else:
            cell = {}
        cells.append({"values": [cell]})
    return {
        "updateCells": {
            "rows": cells,
            "fields": "userEnteredValue,userEnteredFormat.textFormat.link",
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": start - 1,
                "endRowIndex": last,
                "startColumnIndex": LINK_COLUMN,
                "endColumnIndex": LINK_COLUMN + 1,
            },
        }
    }


def spreadsheet_key(url):
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", url.strip()):
        return url.strip()
    raise SheetError("スプレッドシートのURLが正しくありません。")


def service_account_info():
    """認証情報を環境変数、なければ credentials/ のファイルから読む。"""
    text = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if text:
        return json.loads(text)
    if SERVICE_ACCOUNT_PATH.exists():
        return json.loads(SERVICE_ACCOUNT_PATH.read_text(encoding="utf-8"))
    return None


def open_spreadsheet(url):
    import gspread

    info = service_account_info()
    if info is None:
        raise SheetError(f"認証ファイルがありません: {SERVICE_ACCOUNT_PATH}")
    try:
        return gspread.service_account_from_dict(info).open_by_key(spreadsheet_key(url))
    except gspread.exceptions.GSpreadException as e:
        raise SheetError(f"スプレッドシートを開けません（共有設定を確認してください）: {e}") from e


def list_tabs(url):
    return [ws.title for ws in open_spreadsheet(url).worksheets()]


def read_grid(worksheet):
    return worksheet.get_values("A1:H", value_render_option="FORMULA", pad_values=False)


def read_quantities(url, sheet_name):
    """転記先シートの商品名と数量を読む。手で直した個数もここで拾う。"""
    grid = read_grid(open_spreadsheet(url).worksheet(sheet_name))
    quantities = {}
    for row in range(find_data_start(grid), len(grid) + 1):
        name, quantity = _cell(grid, row, 1), _cell(grid, row, 5)
        try:
            if name and float(quantity) > 0:
                quantities[str(name)] = int(float(quantity))
        except ValueError:
            continue
    return quantities


def apply_template(spreadsheet, worksheet):
    """空のシートをテンプレートのコピーに置き換える。タブ名と位置は引き継ぐ。"""
    if not TEMPLATE_SPREADSHEET_ID:
        raise SheetError("転記先のシートが空です。見出しを用意するか、credentials/template.json でテンプレートを指定してください。")
    template = open_spreadsheet(TEMPLATE_SPREADSHEET_ID).get_worksheet_by_id(TEMPLATE_SHEET_ID)
    copied = template.copy_to(spreadsheet.id)
    spreadsheet.batch_update({"requests": [
        {"deleteSheet": {"sheetId": worksheet.id}},
        {"updateSheetProperties": {
            "properties": {"sheetId": copied["sheetId"], "title": worksheet.title, "index": worksheet.index},
            "fields": "title,index",
        }},
    ]})
    return spreadsheet.worksheet(worksheet.title)


def read_links(spreadsheet, worksheet, start, end):
    """D列のセルに付いた購入リンクを、行番号ごとに読む。"""
    if end < start:
        return {}
    meta = spreadsheet.fetch_sheet_metadata({
        "ranges": f"'{worksheet.title}'!D{start}:D{end}",
        "fields": "sheets(data(rowData(values(hyperlink))))",
    })
    data = meta["sheets"][0].get("data", [{}])[0].get("rowData", [])
    links = {}
    for offset, row in enumerate(data):
        values = row.get("values", [])
        if values and values[0].get("hyperlink"):
            links[start + offset] = values[0]["hyperlink"]
    return links


def transcribe(spreadsheet, sheet_name, products, cooperative=False):
    """選んだ商品を転記する。

    cooperative（協力モード）では、今回出力するジャンルの行だけを置き換え、
    ほかのジャンルの行は書かれている内容のまま残す。
    """
    worksheet = spreadsheet.worksheet(sheet_name)
    grid = read_grid(worksheet)
    if is_blank(grid):
        worksheet = apply_template(spreadsheet, worksheet)
        grid = read_grid(worksheet)
    rows = [product_row(p) for p in products]
    if cooperative:
        links = read_links(spreadsheet, worksheet, find_data_start(grid), len(grid))
        rows = merge_rows(kept_rows(grid, links, {p["category"] for p in products}), rows)
    updates, last = build_updates(grid, rows, worksheet.row_count, datetime.now(JST))
    if last > worksheet.row_count:
        worksheet.add_rows(last - worksheet.row_count)
    worksheet.batch_update(updates, raw=False)
    spreadsheet.batch_update({"requests": [build_link_request(grid, rows, last, worksheet.id)]})
