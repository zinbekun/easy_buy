"""購入履歴（直近の転記）の保存と集計。"""
from datetime import datetime

import storage
from config import HISTORY_LIMIT
from sheets import JST

ITEM_FIELDS = ("id", "name", "category", "url", "image", "content", "price")


def load():
    return storage.load("history", [])


def add(products, spreadsheet_url="", sheet_name=""):
    entry = {
        "transcribed_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "spreadsheet_url": spreadsheet_url,
        "sheet_name": sheet_name,
        "items": [{**{key: p[key] for key in ITEM_FIELDS}, "quantity": p.get("quantity", 1)} for p in products],
    }
    storage.save("history", (load() + [entry])[-HISTORY_LIMIT:])


def previous_ids():
    entries = load()
    return [item["id"] for item in entries[-1]["items"]] if entries else []


def rakuchin_plan(entry, products, quantities):
    """前回の転記から、楽チン機能の引き継ぎ内容を作る。

    値段が同じで販売中の商品だけを kept_ids に入れる。
    previous の個数はシートの値を優先し、なければ履歴の値を使う。
    """
    previous = [
        {
            "id": item["id"],
            "category": item["category"],
            "quantity": quantities.get(item["name"], item.get("quantity", 1)),
        }
        for item in entry["items"]
    ]
    kept_ids = [
        item["id"]
        for item in entry["items"]
        if item["id"] in products and round(products[item["id"]]["price"], 2) == round(item["price"], 2)
    ]
    return {"previous": previous, "kept_ids": kept_ids}


def summary(on_sale_ids):
    """商品ごとに重複をまとめ、新しい購入を先頭にして返す。"""
    merged = {}
    for entry in load():
        for item in entry["items"]:
            count = merged[item["id"]]["count"] + 1 if item["id"] in merged else 1
            merged[item["id"]] = {
                **item,
                "count": count,
                "last_at": entry["transcribed_at"],
                "on_sale": item["id"] in on_sale_ids,
            }
    return sorted(merged.values(), key=lambda m: m["last_at"], reverse=True)
