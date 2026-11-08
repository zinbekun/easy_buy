"""買い出しアプリのサーバー。ローカルでも Vercel でも同じコードで動く。

処理の間に状態を持たないよう、商品情報の取得はブラウザから1ページずつ呼び出す。
"""
import hmac
import os
import secrets
import threading
import time
import webbrowser
from datetime import datetime, timedelta

from flask import Flask, jsonify, redirect, request, session

import history
import sheets
import storage
from config import PRICE_LIMIT
from scraper import ScrapeError, Scraper

HOST = "127.0.0.1"
PORT = 8765
DRAFT_TTL_SEC = 3600
LOGIN_PATHS = ("/login",)
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
LOGIN_PAGE = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>買い出しリスト</title>
<style>body{margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center;background:#fff;
color:#333;font:15px/1.5 sans-serif;padding:16px;box-sizing:border-box}form{display:flex;flex-direction:column;
gap:10px;width:min(320px,100%)}input,button{font:inherit;padding:10px;border-radius:4px;border:1px solid #bbb}
button{background:#b90c87;border-color:#b90c87;color:#fff;font-weight:700;cursor:pointer}p{margin:0;color:#c0392b}
</style></head><body><form method="post"><h1 style="font-size:18px;margin:0">買い出しリスト</h1>{message}
<input type="password" name="password" placeholder="パスワード" aria-label="パスワード" autofocus required>
<button>ログイン</button></form></body></html>"""

app = Flask(__name__, static_folder="static", static_url_path="")
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=bool(APP_PASSWORD),
)


def today():
    return datetime.now(sheets.JST)


def load_catalog():
    return storage.load("products", {"items": [], "fetched_at": "", "price_unknown": 0})


def products_by_id():
    return {p["id"]: p for p in load_catalog()["items"]}


def load_settings():
    return storage.load("settings", {"spreadsheet_url": "", "sheet_name": ""})


def service_account_email():
    info = sheets.service_account_info()
    return info.get("client_email", "") if info else ""


@app.before_request
def require_login():
    if not APP_PASSWORD or session.get("logged_in") or request.path in LOGIN_PATHS:
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="ログインが必要です。"), 401
    return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not APP_PASSWORD:
        return redirect("/")
    if request.method == "GET":
        return LOGIN_PAGE.replace("{message}", "")
    if hmac.compare_digest(request.form.get("password", "").encode(), APP_PASSWORD.encode()):
        session.permanent = True
        session["logged_in"] = True
        return redirect("/")
    time.sleep(1)
    return LOGIN_PAGE.replace("{message}", "<p>パスワードが違います。</p>"), 401


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/products")
def api_products():
    catalog = load_catalog()
    return jsonify(
        products=catalog["items"],
        fetched_at=catalog["fetched_at"],
        price_unknown=catalog["price_unknown"],
        stale=not catalog["fetched_at"].startswith(today().strftime("%Y-%m-%d")),
        previous_ids=history.previous_ids(),
        price_limit=PRICE_LIMIT,
    )


@app.post("/api/crawl")
def api_crawl():
    """1ページ取得して途中結果に足し、最後のページで商品情報を置き換える。

    1ページでも失敗すれば置き換えない。一部だけで更新すると選択が消えてしまうため。
    """
    data = request.get_json()
    run_id = "".join(c for c in str(data["run_id"]) if c.isalnum() or c == "-")[:64]
    key = f"draft-{run_id}"
    try:
        step = Scraper().fetch_page(int(data["source"]), int(data["page"]))
    except ScrapeError as e:
        storage.delete(key)
        return jsonify(error=str(e)), 502
    draft = storage.load(key, {"items": {}, "unknown": 0})
    for item in step["items"]:
        draft["items"].setdefault(item["id"], item)
    draft["unknown"] += step["unknown"]
    if step["next"]:
        storage.save(key, draft, ttl_sec=DRAFT_TTL_SEC)
    else:
        storage.save("products", {
            "items": list(draft["items"].values()),
            "fetched_at": today().strftime("%Y-%m-%d %H:%M"),
            "price_unknown": draft["unknown"],
        })
        storage.delete(key)
    return jsonify(progress=step["progress"], next=step["next"])


@app.get("/api/settings")
def api_get_settings():
    return jsonify(**load_settings(), service_account_email=service_account_email())


@app.post("/api/settings")
def api_post_settings():
    data = request.get_json()
    storage.save("settings", {
        "spreadsheet_url": data.get("spreadsheet_url", "").strip(),
        "sheet_name": data.get("sheet_name", ""),
    })
    return jsonify(ok=True)


@app.get("/api/sheets")
def api_sheets():
    url = load_settings()["spreadsheet_url"]
    if not url:
        return jsonify(tabs=[])
    try:
        return jsonify(tabs=sheets.list_tabs(url))
    except sheets.SheetError as e:
        return jsonify(error=str(e)), 400


@app.post("/api/transcribe")
def api_transcribe():
    data = request.get_json()
    ids, sheet_name = data.get("ids", []), data.get("sheet_name", "")
    settings = load_settings()
    if not settings["spreadsheet_url"] or not sheet_name:
        return jsonify(error="転記先のスプレッドシートとシートを指定してください。"), 400
    products = products_by_id()
    selected = [products[i] for i in ids if i in products]
    if not selected or len(selected) != len(ids):
        return jsonify(error="販売中でない商品が含まれています。商品情報を更新してやり直してください。"), 409
    quantities = data.get("quantities", {})
    selected = sheets.transcribe_order([{**p, "quantity": quantities.get(p["id"], 1)} for p in selected])
    cooperative = bool(data.get("cooperative"))
    try:
        sheets.transcribe(sheets.open_spreadsheet(settings["spreadsheet_url"]), sheet_name, selected, cooperative)
    except sheets.SheetError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        return jsonify(error=f"転記に失敗しました: {type(e).__name__}: {e}"), 502
    history.add(selected, settings["spreadsheet_url"], sheet_name)
    storage.save("settings", {**settings, "sheet_name": sheet_name})
    return jsonify(count=len(selected))


@app.post("/api/rakuchin")
def api_rakuchin():
    entries = history.load()
    if not entries:
        return jsonify(error="前回の購入履歴がないため、楽チン機能は使えません。"), 400
    entry, settings = entries[-1], load_settings()
    url = entry.get("spreadsheet_url") or settings["spreadsheet_url"]
    sheet_name = entry.get("sheet_name") or settings["sheet_name"]
    warning = ""
    try:
        quantities = sheets.read_quantities(url, sheet_name)
    except Exception as e:
        quantities = {}
        warning = f"シートから個数を読めなかったため、履歴の個数を使います（{type(e).__name__}）。"
    return jsonify(**history.rakuchin_plan(entry, products_by_id(), quantities), warning=warning)


@app.get("/api/history")
def api_history():
    return jsonify(items=history.summary(set(products_by_id())))


if __name__ == "__main__":
    # コードを書き換えたらサーバーを自動で読み込み直す。古いコードのまま動き続けないようにするため。
    # 読み込み直しは子プロセスで行われるので、ブラウザを開くのは最初の親プロセスだけにする
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        threading.Timer(1.5, webbrowser.open, [f"http://{HOST}:{PORT}/"]).start()
        print(f"http://{HOST}:{PORT}/ を開いてください（終了は Ctrl+C）")
    app.run(host=HOST, port=PORT, threaded=True, use_reloader=True)
