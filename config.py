"""アプリ全体の定数。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SERVICE_ACCOUNT_PATH = ROOT / "credentials" / "service_account.json"

STORE_CODE = "01050000006660"
BASE_URL = f"https://shop.aeon.com/netsuper/{STORE_CODE}"
USER_AGENT = "aeon-kaidashi/1.0 (personal shopping list tool)"

# 税込みでこの金額未満の商品だけを表示する
PRICE_LIMIT = 128
DAILY_CATEGORY = "日用品"
CATEGORIES = ("お菓子", "カップ麺", "飲み物", DAILY_CATEGORY)

# 再試行込みでも1ページの処理が長引きすぎないようにする
REQUEST_TIMEOUT_SEC = 10
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 3
MAX_PAGES_PER_CATEGORY = 60

# True にするとカップ麺を売場 1G/1G05.html から正確に取得する。
# ただしこのURLは robots.txt の Disallow: /netsuper/*/*/ に該当する。
USE_CUP_NOODLE_SUBCATEGORY = False

# 空のシートへ転記するときに丸ごとコピーするテンプレート。共有スプレッドシートのIDなので
# リポジトリには含めず、credentials/template.json に {"spreadsheet_id": ..., "sheet_id": ...} で置く
TEMPLATE_PATH = ROOT / "credentials" / "template.json"


def _load_template():
    try:
        template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "", 0
    return template["spreadsheet_id"], int(template["sheet_id"])


TEMPLATE_SPREADSHEET_ID, TEMPLATE_SHEET_ID = _load_template()

HISTORY_LIMIT = 5
