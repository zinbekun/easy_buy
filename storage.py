"""履歴・設定・商品情報の保存先。

KV_REST_API_URL（Vercel の Upstash 連携）があれば Redis、なければ data/ のファイルを使う。
"""
import json
import os

import requests

from config import DATA_DIR

REDIS_URL = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
REDIS_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
PREFIX = "aeon-kaidashi:"


def _redis(*command):
    res = requests.post(REDIS_URL, json=list(command), headers={"Authorization": f"Bearer {REDIS_TOKEN}"}, timeout=10)
    res.raise_for_status()
    return res.json()["result"]


def _path(key):
    return DATA_DIR / f"{key}.json"


def load(key, default=None):
    if REDIS_URL:
        value = _redis("GET", PREFIX + key)
        return default if value is None else json.loads(value)
    try:
        return json.loads(_path(key).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def save(key, value, ttl_sec=None):
    text = json.dumps(value, ensure_ascii=False)
    if REDIS_URL:
        _redis("SET", PREFIX + key, text, *(("EX", ttl_sec) if ttl_sec else ()))
        return
    DATA_DIR.mkdir(exist_ok=True)
    tmp = _path(key).with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, _path(key))


def delete(key):
    if REDIS_URL:
        _redis("DEL", PREFIX + key)
    else:
        _path(key).unlink(missing_ok=True)
