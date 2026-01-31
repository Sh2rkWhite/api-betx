import json
import os
import time
import secrets
import threading
import base64
from typing import Any, Dict, Optional

from flask import Flask, jsonify, request

import requests

try:
    from pymongo import MongoClient
except Exception:  # pragma: no cover
    MongoClient = None

MONGODB_URI_FALLBACK = ""

from wins_db import apply_client_tx, get_balance_client
from wins_db import apply_coin_tx, get_coin_balance_client, ton_to_coins
from wins_db import get_game_flags, init_wins_db, insert_win, list_active_promos, list_wins, redeem_promo_client, set_game_enabled
from wins_db import list_cases, open_case
from wins_db import create_payment_intent, get_payment_intent, get_payment_intent_by_tg_charge, mark_payment_intent_paid, update_payment_intent
from wins_db import list_topups
from wins_db import bind_referral, get_referral_code, get_referral_overview, touch_ref_client

from wins_db import (
    admin_add_case_drop,
    admin_delete_case_drop,
    admin_list_case_drops,
    admin_list_cases,
    admin_list_contests,
    contests_buy_tickets,
    contests_finalize_due,
    admin_list_coin_transactions,
    admin_list_top_coin_balances,
    admin_update_case,
    create_promo,
    get_app_setting,
    set_app_setting,
    validate_admin_session,
    revoke_admin_session,
    apply_gift_tx,
    create_support_request,
)

from crash_engine import get_crash_engine


TONPAY_DEFAULT_TIMEOUT_SEC = 5 * 60
TONPAY_DEFAULT_MIN_AMOUNT_TON = 0.05


_tonpay_lock = threading.Lock()
_tonpay_sessions: Dict[str, Dict[str, Any]] = {}
_tonpay_used_tx_hashes: Dict[str, int] = {}


def _tonpay_cleanup(now_ts: Optional[int] = None) -> None:
    now = int(now_ts or time.time())
    with _tonpay_lock:
        expired_comments = []
        for comment, sess in _tonpay_sessions.items():
            try:
                if int(sess.get("expires_ts") or 0) <= now:
                    expired_comments.append(comment)
            except Exception:
                expired_comments.append(comment)
        for c in expired_comments:
            try:
                del _tonpay_sessions[c]
            except Exception:
                pass

        expired_hashes = []
        for h, ts in _tonpay_used_tx_hashes.items():
            try:
                if int(ts or 0) <= now - 24 * 3600:
                    expired_hashes.append(h)
            except Exception:
                expired_hashes.append(h)
        for h in expired_hashes:
            try:
                del _tonpay_used_tx_hashes[h]
            except Exception:
                pass


def _tonpay_random_tag() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(4))


def _tonpay_make_comment(user_id: str) -> str:
    uid = str(user_id or "").strip() or "0"
    return f"WEBPAY-{uid}-{_tonpay_random_tag()}"


def _tonpay_api_headers() -> Dict[str, str]:
    key = os.getenv("TONAPI_KEY") or os.getenv("TONAPI_TOKEN")
    if key:
        return {"Authorization": f"Bearer {key}"}
    return {}


def _tonpay_get_deposit_address() -> str:
    return str(os.getenv("TON_DEPOSIT_ADDRESS") or "").strip()


def _tonpay_get_min_amount_ton() -> float:
    raw = os.getenv("TONPAY_MIN_AMOUNT_TON")
    if not raw:
        return TONPAY_DEFAULT_MIN_AMOUNT_TON
    try:
        v = float(str(raw).replace(",", "."))
        return v if v > 0 else TONPAY_DEFAULT_MIN_AMOUNT_TON
    except Exception:
        return TONPAY_DEFAULT_MIN_AMOUNT_TON


def _tonpay_get_timeout_sec() -> int:
    raw = os.getenv("TONPAY_TIMEOUT_SEC")
    if not raw:
        return TONPAY_DEFAULT_TIMEOUT_SEC
    try:
        v = int(raw)
        return v if v > 0 else TONPAY_DEFAULT_TIMEOUT_SEC
    except Exception:
        return TONPAY_DEFAULT_TIMEOUT_SEC


def _tonpay_ton_to_nano(amount_ton: float) -> int:
    try:
        return int(round(float(amount_ton) * 1_000_000_000))
    except Exception:
        return 0


def _tonpay_extract_comment(tx: Dict[str, Any]) -> str:
    msg = tx.get("in_msg") if isinstance(tx.get("in_msg"), dict) else {}
    candidates = []
    for k in ("message", "comment", "text", "body"):
        v = msg.get(k)
        if isinstance(v, str) and v.strip():
            candidates.append(v.strip())
    decoded = msg.get("decoded_body")
    if isinstance(decoded, dict):
        t = decoded.get("text")
        if isinstance(t, str) and t.strip():
            candidates.append(t.strip())
    for s in candidates:
        if s.startswith("WEBPAY-"):
            return s
        if "WEBPAY-" in s:
            i = s.find("WEBPAY-")
            return s[i : i + 64].strip()
    return ""


def _tonpay_tx_hash(tx: Dict[str, Any]) -> str:
    h = tx.get("hash")
    if isinstance(h, str) and h.strip():
        return h.strip()
    tid = tx.get("transaction_id")
    if isinstance(tid, dict):
        hh = tid.get("hash")
        if isinstance(hh, str) and hh.strip():
            return hh.strip()
    return ""


def _tonpay_is_incoming_to_wallet(tx: Dict[str, Any], wallet: str) -> bool:
    msg = tx.get("in_msg") if isinstance(tx.get("in_msg"), dict) else {}
    dst = msg.get("destination") or msg.get("dst") or tx.get("account")
    if not isinstance(dst, str) or not dst.strip():
        return False
    return str(dst).strip() == str(wallet).strip()


def _tonpay_incoming_value_nano(tx: Dict[str, Any]) -> int:
    msg = tx.get("in_msg") if isinstance(tx.get("in_msg"), dict) else {}
    v = msg.get("value") or msg.get("amount")
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    try:
        return int(str(v).strip())
    except Exception:
        return 0


def _tonpay_get_wallet_transactions(wallet: str, limit: int = 20) -> Any:
    url = f"https://tonapi.io/v2/accounts/{wallet}/transactions"
    r = requests.get(url, params={"limit": int(limit)}, headers=_tonpay_api_headers(), timeout=12)
    if not (200 <= r.status_code < 300):
        raise RuntimeError(f"tonapi failed: {r.status_code}")
    return r.json()


def _tonpay_find_matching_tx(wallet: str, expected_comment: str, expected_amount_ton: float) -> Optional[Dict[str, Any]]:
    expected_nano = _tonpay_ton_to_nano(expected_amount_ton)
    data = _tonpay_get_wallet_transactions(wallet=wallet, limit=30)

    items = None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        if isinstance(data.get("transactions"), list):
            items = data.get("transactions")
        elif isinstance(data.get("items"), list):
            items = data.get("items")
        else:
            items = data.get("transactions")
    if not isinstance(items, list):
        return None

    for tx in items:
        if not isinstance(tx, dict):
            continue
        if not _tonpay_is_incoming_to_wallet(tx=tx, wallet=wallet):
            continue
        c = _tonpay_extract_comment(tx)
        if not c or c != expected_comment:
            continue
        amt_nano = _tonpay_incoming_value_nano(tx)
        if expected_nano > 0 and amt_nano < expected_nano:
            continue
        h = _tonpay_tx_hash(tx)
        if not h:
            continue
        with _tonpay_lock:
            if h in _tonpay_used_tx_hashes:
                continue
        return tx
    return None


def _safe_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _safe_json(v: Any) -> Optional[str]:
    if v is None:
        return None
    try:
        return json.dumps(v, ensure_ascii=False)
    except Exception:
        return None


def _process_contest_finalizations() -> None:
    finalized = []
    try:
        finalized = contests_finalize_due(now_ts=int(time.time()))
    except Exception:
        finalized = []

    if not finalized:
        return

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    announce_chat = os.getenv("CONTESTS_ANNOUNCE_CHAT_ID") or os.getenv("ADMIN_NOTIFY_CHAT_ID")

    for f in finalized:
        try:
            insert_win(
                ts=int(time.time()),
                player_name=str(f.get("winner_name") or ""),
                win_type="contest",
                game="contests",
                gift_id=str(f.get("prize_gift_id") or "") or None,
                gift_name=str(f.get("title") or "") or None,
                amount_ton=None,
                meta_json=json.dumps({"contest_id": f.get("contest_id")}, ensure_ascii=False),
            )
        except Exception:
            pass

        try:
            winner_id = int(f.get("winner_tg_user_id") or 0)
        except Exception:
            winner_id = 0
        winner_name = str(f.get("winner_name") or "").strip()
        title = str(f.get("title") or f.get("contest_id") or "Конкурс")
        prize = f.get("prize_gift_id")
        prize_txt = f"Приз: {prize}" if prize else "Приз: подарок"

        if bot_token and winner_id > 0:
            try:
                _tg_api_post(
                    "sendMessage",
                    {"chat_id": winner_id, "text": f"🏆 Вы выиграли конкурс!\n{title}\n{prize_txt}"},
                )
            except Exception:
                pass

        if announce_chat:
            try:
                if winner_name:
                    msg = f"🏁 Конкурс завершен: {title}\nПобедитель: {winner_name}"
                else:
                    msg = f"🏁 Конкурс завершен: {title}\nУчастников не было"
                _tg_api_post("sendMessage", {"chat_id": int(announce_chat), "text": msg})
            except Exception:
                pass


def _start_contests_scheduler() -> None:
    if getattr(_start_contests_scheduler, "_started", False):
        return
    setattr(_start_contests_scheduler, "_started", True)

    def _loop() -> None:
        while True:
            try:
                _process_contest_finalizations()
            except Exception:
                pass
            time.sleep(10)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()


def _admin_token_from_request() -> Optional[str]:
    auth = request.headers.get("Authorization")
    if auth:
        a = str(auth).strip()
        if a.lower().startswith("bearer "):
            tok = a[7:].strip()
            return tok or None
    for k in ("X-Admin-Token", "X-Admin", "X-Token"):
        v = request.headers.get(k)
        if v:
            tok = str(v).strip()
            if tok:
                return tok
    q = request.args.get("token")
    if q:
        tok = str(q).strip()
        if tok:
            return tok
    try:
        data = request.get_json(silent=True) or {}
        if isinstance(data, dict) and data.get("token"):
            tok = str(data.get("token") or "").strip()
            if tok:
                return tok
    except Exception:
        pass
    return None


def _require_admin() -> Dict[str, Any]:
    tok = _admin_token_from_request()
    if not tok:
        raise PermissionError("missing admin token")
    sess = validate_admin_session(token=tok)
    if not sess:
        raise PermissionError("invalid admin token")
    return sess


def _admin_error(e: Exception) -> Any:
    msg = str(e) or "forbidden"
    if isinstance(e, PermissionError):
        return jsonify({"ok": False, "error": msg}), 401
    return jsonify({"ok": False, "error": msg}), 400


def _users_db_path() -> str:
    p = os.getenv("USERS_DB_PATH", "bot_users.sqlite3")
    if p == "bot_users.sqlite3":
        p = os.path.join(os.path.dirname(__file__), "bot_users.sqlite3")
    return p


def _read_users(limit: int = 200) -> Any:
    import sqlite3

    conn = sqlite3.connect(_users_db_path())
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT user_id, username, first_name, last_name, first_seen_ts, last_seen_ts
            FROM users
            ORDER BY last_seen_ts DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _count_users() -> int:
    import sqlite3

    conn = sqlite3.connect(_users_db_path())
    try:
        row = conn.execute("SELECT COUNT(1) AS c FROM users").fetchone()
        if not row:
            return 0
        try:
            return int(row[0] or 0)
        except Exception:
            return 0
    finally:
        conn.close()


app = Flask(__name__)


_mongo_client: Optional[Any] = None


def _get_mongo_client() -> Any:
    global _mongo_client
    if _mongo_client is not None:
        return _mongo_client
    if MongoClient is None:
        raise RuntimeError("MongoDB support is not available")
    uri = os.getenv("MONGODB_URI") or MONGODB_URI_FALLBACK
    if not uri:
        raise RuntimeError("MongoDB is disabled")
    _mongo_client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    return _mongo_client


def _get_mongo_db_name() -> str:
    # Let user override DB name explicitly; otherwise rely on URI default.
    v = os.getenv("MONGODB_DB")
    return str(v).strip() if v and str(v).strip() else "test"


def _mongo_clients_col():
    client = _get_mongo_client()
    db = client.get_database(_get_mongo_db_name())
    return db.get_collection("clients")


def _mongo_upsert_client(*, client_id: str) -> None:
    cid = _safe_str(client_id)
    if not cid:
        return
    try:
        col = _mongo_clients_col()
        now = int(time.time())
        ip = _safe_str(request.headers.get("X-Forwarded-For")) or _safe_str(getattr(request, "remote_addr", None))
        ua = _safe_str(request.headers.get("User-Agent"))
        col.update_one(
            {"client_id": cid},
            {
                "$setOnInsert": {"client_id": cid, "created_ts": now},
                "$set": {"last_seen_ts": now, "last_ip": ip, "last_user_agent": ua},
            },
            upsert=True,
        )
    except Exception:
        # Mongo must never break core API calls
        pass


@app.get("/api/mongo/ping")
def api_mongo_ping() -> Any:
    try:
        client = _get_mongo_client()
        client.admin.command("ping")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "failed"}), 400


@app.get("/api/admin/mongo/clients")
def api_admin_mongo_clients_list() -> Any:
    try:
        _require_admin()
        col = _mongo_clients_col()
        limit = request.args.get("limit")
        try:
            n = int(limit) if limit is not None else 50
        except Exception:
            n = 50
        n = max(1, min(200, n))
        rows = list(col.find({}, {"_id": 0}).sort("last_seen_ts", -1).limit(n))
        return jsonify({"ok": True, "items": rows})
    except Exception as e:
        return _admin_error(e)


@app.get("/api/admin/mongo/client")
def api_admin_mongo_client_get() -> Any:
    try:
        _require_admin()
        cid = _safe_str(request.args.get("client_id"))
        if not cid:
            return jsonify({"ok": False, "error": "client_id is required"}), 400
        col = _mongo_clients_col()
        doc = col.find_one({"client_id": cid}, {"_id": 0})
        return jsonify({"ok": True, "client": doc})
    except Exception as e:
        return _admin_error(e)


@app.post("/api/admin/mongo/client/update")
def api_admin_mongo_client_update_post() -> Any:
    try:
        _require_admin()
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        cid = _safe_str(data.get("client_id"))
        if not cid:
            return jsonify({"ok": False, "error": "client_id is required"}), 400

        # Allow only safe fields to edit
        patch: Dict[str, Any] = {}
        for k in ("note", "label", "wallet", "tg_user_id", "tg_username"):
            if k in data:
                v = data.get(k)
                patch[k] = v

        if not patch:
            return jsonify({"ok": False, "error": "no fields to update"}), 400

        col = _mongo_clients_col()
        col.update_one({"client_id": cid}, {"$set": patch, "$setOnInsert": {"client_id": cid, "created_ts": int(time.time())}}, upsert=True)
        doc = col.find_one({"client_id": cid}, {"_id": 0})
        return jsonify({"ok": True, "client": doc})
    except Exception as e:
        return _admin_error(e)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _env_required(key: str) -> str:
    v = os.getenv(key)
    if not v:
        raise RuntimeError(f"{key} is not set")
    return str(v)


def _get_crypto_pay_headers() -> Dict[str, str]:
    token = _env_required("CRYPTO_PAY_TOKEN")
    return {"Crypto-Pay-API-Token": token, "Content-Type": "application/json"}


def _crypto_pay_post(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    base = os.getenv("CRYPTO_PAY_API", "https://pay.crypt.bot/api")
    url = f"{base.rstrip('/')}/{method.lstrip('/')}"
    r = requests.post(url, json=payload, headers=_get_crypto_pay_headers(), timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data or not isinstance(data, dict) or not data.get("ok"):
        raise ValueError("CryptoBot API error")
    return data


def _tg_api_post(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    url = f"https://api.telegram.org/bot{token}/{method.lstrip('/')}"
    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data or not isinstance(data, dict) or not data.get("ok"):
        desc = None
        code = None
        try:
            desc = data.get("description") if isinstance(data, dict) else None
            code = data.get("error_code") if isinstance(data, dict) else None
        except Exception:
            desc = None
            code = None
        if desc and code:
            raise ValueError(f"Telegram API error {code}: {desc}")
        if desc:
            raise ValueError(f"Telegram API error: {desc}")
        raise ValueError("Telegram API error")
    return data


@app.after_request
def add_cors_headers(resp: Any) -> Any:
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization,X-Admin-Token,X-Admin,X-Token"
    return resp


@app.before_request
def mongo_touch_client() -> None:
    # Best-effort: create/update client document in Mongo when client_id is present.
    # Must not break existing API if Mongo is down.
    try:
        if request.method == "OPTIONS":
            return
        p = str(getattr(request, "path", "") or "")
        if not p.startswith("/api/"):
            return
        # skip admin endpoints (no client tracking needed)
        if p.startswith("/api/admin/"):
            return

        cid = _safe_str(request.args.get("client_id"))
        if not cid:
            data = request.get_json(silent=True) or {}
            if isinstance(data, dict):
                cid = _safe_str(data.get("client_id"))
        if cid:
            _mongo_upsert_client(client_id=cid)
    except Exception:
        return


@app.route("/api/wins", methods=["OPTIONS"])
def api_wins_options() -> Any:
    return ("", 204)


@app.route("/api/<path:_>", methods=["OPTIONS"])
def api_any_options(_: str) -> Any:
    return ("", 204)


@app.get("/api/admin/me")
def api_admin_me() -> Any:
    try:
        sess = _require_admin()
    except Exception as e:
        return _admin_error(e)
    return jsonify({"ok": True, "session": sess})


@app.get("/api/stats")
def api_stats_get() -> Any:
    try:
        users_total = _count_users()
    except Exception:
        users_total = 0
    return jsonify({"ok": True, "users_total": int(users_total)})


@app.get("/api/contests")
def api_contests_get() -> Any:
    try:
        init_wins_db()
        _start_contests_scheduler()
        _process_contest_finalizations()

        items = admin_list_contests(include_disabled=False)
        now = int(time.time())
        items = [c for c in items if int(c.get("ends_ts") or 0) > (now - 60 * 60 * 24)]
        return jsonify({"ok": True, "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "failed"}), 400


@app.post("/api/inventory/adjust")
def api_inventory_adjust_post() -> Any:
    init_wins_db()
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    client_id = _safe_str(data.get("client_id"))
    gift_id = _safe_str(data.get("gift_id"))
    delta_raw = data.get("qty_delta")
    tx_type = _safe_str(data.get("tx_type")) or "client_adjust"
    if not client_id or not gift_id or delta_raw is None:
        return jsonify({"ok": False, "error": "client_id, gift_id, qty_delta are required"}), 400
    try:
        qty_delta = int(delta_raw)
    except Exception:
        return jsonify({"ok": False, "error": "qty_delta must be int"}), 400
    if qty_delta == 0:
        return jsonify({"ok": False, "error": "qty_delta must be != 0"}), 400
    idem = f"gift:{int(time.time())}:{client_id}:{gift_id}:{qty_delta}:{secrets.token_hex(4)}"
    try:
        new_qty = apply_gift_tx(
            client_id=client_id,
            gift_id=gift_id,
            qty_delta=qty_delta,
            tx_type=tx_type,
            idempotency_key=idem,
            meta_json=_safe_json({"source": "client", "gift_id": gift_id, "qty_delta": qty_delta}),
        )
        return jsonify({"ok": True, "client_id": client_id, "gift_id": gift_id, "qty": int(new_qty)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "failed"}), 400


@app.get("/api/inventory")
def api_inventory_get() -> Any:
    init_wins_db()
    client_id = _safe_str(request.args.get("client_id"))
    if not client_id:
        return jsonify({"ok": False, "error": "client_id is required"}), 400
    try:
        from wins_db import list_gift_inventory_client

        items = list_gift_inventory_client(client_id=client_id)
        return jsonify({"ok": True, "client_id": client_id, "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "failed"}), 400


@app.post("/api/admin/inventory/adjust")
def api_admin_inventory_adjust_post() -> Any:
    try:
        _require_admin()
        init_wins_db()
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        client_id = _safe_str(data.get("client_id"))
        gift_id = _safe_str(data.get("gift_id"))
        delta_raw = data.get("qty_delta")
        tx_type = _safe_str(data.get("tx_type")) or "admin_adjust"
        if not client_id or not gift_id or delta_raw is None:
            return jsonify({"ok": False, "error": "client_id, gift_id, qty_delta are required"}), 400
        qty_delta = int(delta_raw)
        if qty_delta == 0:
            return jsonify({"ok": False, "error": "qty_delta must be != 0"}), 400
        idem = f"admin_gift:{int(time.time())}:{client_id}:{gift_id}:{qty_delta}"
        new_qty = apply_gift_tx(
            client_id=client_id,
            gift_id=gift_id,
            qty_delta=qty_delta,
            tx_type=tx_type,
            idempotency_key=idem,
            meta_json=_safe_json({"source": "admin", "gift_id": gift_id, "qty_delta": qty_delta}),
        )
        return jsonify({"ok": True, "client_id": client_id, "gift_id": gift_id, "qty": int(new_qty)})
    except Exception as e:
        return _admin_error(e)


@app.post("/api/support/request")
def api_support_request_post() -> Any:
    init_wins_db()
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    user_id_raw = data.get("user_id")
    chat_id_raw = data.get("chat_id")
    username = _safe_str(data.get("username"))
    try:
        user_id = int(user_id_raw)
        chat_id = int(chat_id_raw)
    except Exception:
        return jsonify({"ok": False, "error": "user_id and chat_id must be ints"}), 400
    if user_id <= 0 or chat_id <= 0:
        return jsonify({"ok": False, "error": "user_id and chat_id are required"}), 400
    try:
        sess = create_support_request(user_id=user_id, user_chat_id=chat_id, user_username=username)
        return jsonify({"ok": True, "session": sess})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "failed"}), 400


@app.post("/api/contests/buy")
def api_contests_buy_post() -> Any:
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    contest_id = _safe_str(data.get("contest_id"))
    qty_raw = data.get("qty")
    tg_user_id_raw = data.get("tg_user_id")
    tg_username = _safe_str(data.get("tg_username"))
    tg_first_name = _safe_str(data.get("tg_first_name"))
    tg_last_name = _safe_str(data.get("tg_last_name"))

    if not contest_id:
        return jsonify({"ok": False, "error": "contest_id is required"}), 400
    try:
        qty = int(qty_raw)
    except Exception:
        qty = 0
    try:
        tg_user_id = int(tg_user_id_raw)
    except Exception:
        tg_user_id = 0
    if qty <= 0:
        return jsonify({"ok": False, "error": "qty must be > 0"}), 400
    if tg_user_id <= 0:
        return jsonify({"ok": False, "error": "tg_user_id is required"}), 400

    try:
        init_wins_db()
        _start_contests_scheduler()
        _process_contest_finalizations()
        res = contests_buy_tickets(
            contest_id=contest_id,
            tg_user_id=tg_user_id,
            qty=qty,
            tg_username=tg_username,
            tg_first_name=tg_first_name,
            tg_last_name=tg_last_name,
        )
        _process_contest_finalizations()
        return jsonify({"ok": True, "result": res})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "failed"}), 400


@app.post("/api/admin/logout")
def api_admin_logout() -> Any:
    tok = _admin_token_from_request()
    if tok:
        try:
            revoke_admin_session(token=tok)
        except Exception:
            pass
    return jsonify({"ok": True})


@app.get("/api/admin/wins")
def api_admin_wins_get() -> Any:
    try:
        _require_admin()
        init_wins_db()
        limit_raw = request.args.get("limit", "50")
        limit = max(1, min(200, int(limit_raw)))
        return jsonify({"ok": True, "items": list_wins(limit=limit)})
    except Exception as e:
        return _admin_error(e)


@app.get("/api/admin/users")
def api_admin_users_get() -> Any:
    try:
        _require_admin()
        limit_raw = request.args.get("limit", "200")
        limit = max(1, min(500, int(limit_raw)))
        items = _read_users(limit=limit)
        return jsonify({"ok": True, "items": items})
    except Exception as e:
        return _admin_error(e)


@app.get("/api/admin/settings")
def api_admin_settings_get() -> Any:
    try:
        _require_admin()
        init_wins_db()
        out = {
            "case.loss_mid_threshold": get_app_setting(key="case.loss_mid_threshold", default="2"),
            "case.loss_high_threshold": get_app_setting(key="case.loss_high_threshold", default="4"),
            "case.demo_weight_mult": get_app_setting(key="case.demo_weight_mult", default="1.4"),
        }
        return jsonify({"ok": True, "settings": out})
    except Exception as e:
        return _admin_error(e)


@app.post("/api/admin/settings")
def api_admin_settings_post() -> Any:
    try:
        _require_admin()
        init_wins_db()
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        key = _safe_str(data.get("key"))
        value = data.get("value")
        if not key:
            return jsonify({"ok": False, "error": "key is required"}), 400
        set_app_setting(key=key, value=str(value if value is not None else ""))
        return jsonify({"ok": True, "key": key, "value": get_app_setting(key=key, default=None)})
    except Exception as e:
        return _admin_error(e)


@app.get("/api/admin/game-flags")
def api_admin_game_flags_get() -> Any:
    try:
        _require_admin()
        init_wins_db()
        return jsonify({"ok": True, "flags": get_game_flags()})
    except Exception as e:
        return _admin_error(e)


@app.post("/api/admin/game-flags")
def api_admin_game_flags_post() -> Any:
    try:
        _require_admin()
        init_wins_db()
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        game = _safe_str(data.get("game"))
        enabled_raw = data.get("enabled")
        enabled = bool(enabled_raw)
        if enabled_raw in (0, "0", "false", "False", False, None):
            enabled = False
        if not game:
            return jsonify({"ok": False, "error": "game is required"}), 400
        set_game_enabled(game=game, enabled=enabled)
        return jsonify({"ok": True, "flags": get_game_flags()})
    except Exception as e:
        return _admin_error(e)


@app.get("/api/admin/cases")
def api_admin_cases_get() -> Any:
    try:
        _require_admin()
        init_wins_db()
        return jsonify({"ok": True, "items": admin_list_cases()})
    except Exception as e:
        return _admin_error(e)


@app.post("/api/admin/cases/update")
def api_admin_cases_update_post() -> Any:
    try:
        _require_admin()
        init_wins_db()
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        case_id = _safe_str(data.get("case_id"))
        if not case_id:
            return jsonify({"ok": False, "error": "case_id is required"}), 400

        name = data.get("name")
        price = data.get("price_coins")
        enabled_raw = data.get("enabled")
        enabled: Optional[bool] = None
        if enabled_raw is not None:
            enabled = bool(enabled_raw)
            if enabled_raw in (0, "0", "false", "False", False):
                enabled = False

        price_coins: Optional[int] = None
        if price is not None:
            price_coins = int(float(str(price).replace(",", ".")))

        admin_update_case(
            case_id=case_id,
            name=str(name).strip() if name is not None else None,
            price_coins=price_coins,
            enabled=enabled,
        )
        return jsonify({"ok": True, "items": admin_list_cases()})
    except Exception as e:
        return _admin_error(e)


@app.get("/api/admin/cases/drops")
def api_admin_case_drops_get() -> Any:
    try:
        _require_admin()
        init_wins_db()
        case_id = _safe_str(request.args.get("case_id"))
        if not case_id:
            return jsonify({"ok": False, "error": "case_id is required"}), 400
        return jsonify({"ok": True, "items": admin_list_case_drops(case_id=case_id)})
    except Exception as e:
        return _admin_error(e)


@app.post("/api/admin/cases/drops/add")
def api_admin_case_drops_add_post() -> Any:
    try:
        _require_admin()
        init_wins_db()
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        case_id = _safe_str(data.get("case_id"))
        reward_type = _safe_str(data.get("reward_type"))
        reward_id = _safe_str(data.get("reward_id"))
        payout = data.get("payout_coins")
        weight = data.get("weight")
        tier = _safe_str(data.get("tier"))
        if not case_id or not reward_type or weight is None:
            return jsonify({"ok": False, "error": "case_id, reward_type and weight are required"}), 400
        payout_coins = int(float(str(payout or 0).replace(",", ".")))
        w = float(str(weight).replace(",", "."))
        did = admin_add_case_drop(
            case_id=case_id,
            reward_type=reward_type,
            reward_id=reward_id,
            payout_coins=payout_coins,
            weight=w,
            tier=tier,
        )
        return jsonify({"ok": True, "id": int(did), "items": admin_list_case_drops(case_id=case_id)})
    except Exception as e:
        return _admin_error(e)


@app.post("/api/admin/cases/drops/delete")
def api_admin_case_drops_delete_post() -> Any:
    try:
        _require_admin()
        init_wins_db()
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        drop_id_raw = data.get("drop_id")
        if drop_id_raw is None:
            return jsonify({"ok": False, "error": "drop_id is required"}), 400
        did = int(drop_id_raw)
        admin_delete_case_drop(drop_id=did)
        return jsonify({"ok": True})
    except Exception as e:
        return _admin_error(e)


@app.get("/api/admin/economy/top-coins")
def api_admin_top_coins_get() -> Any:
    try:
        _require_admin()
        init_wins_db()
        limit_raw = request.args.get("limit", "20")
        limit = max(1, min(100, int(limit_raw)))
        return jsonify({"ok": True, "items": admin_list_top_coin_balances(limit=limit)})
    except Exception as e:
        return _admin_error(e)


@app.get("/api/admin/economy/coin-txs")
def api_admin_coin_txs_get() -> Any:
    try:
        _require_admin()
        init_wins_db()
        limit_raw = request.args.get("limit", "50")
        limit = max(1, min(200, int(limit_raw)))
        client_id = _safe_str(request.args.get("client_id"))
        return jsonify({"ok": True, "items": admin_list_coin_transactions(limit=limit, client_id=client_id)})
    except Exception as e:
        return _admin_error(e)


@app.post("/api/admin/economy/adjust-coin")
def api_admin_adjust_coin_post() -> Any:
    try:
        _require_admin()
        init_wins_db()
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        client_id = _safe_str(data.get("client_id"))
        delta_raw = data.get("delta_coins")
        if not client_id or delta_raw is None:
            return jsonify({"ok": False, "error": "client_id and delta_coins are required"}), 400
        delta = int(float(str(delta_raw).replace(",", ".")))
        if delta == 0:
            return jsonify({"ok": False, "error": "delta_coins must be != 0"}), 400
        idem = f"admin_coin:{int(time.time())}:{client_id}:{delta}"
        bal = apply_coin_tx(client_id=client_id, amount_coins=delta, tx_type="admin_adjust", idempotency_key=idem, meta_json=None)
        return jsonify({"ok": True, "client_id": client_id, "balance_coins": int(bal)})
    except Exception as e:
        return _admin_error(e)


@app.post("/api/admin/economy/adjust-ton")
def api_admin_adjust_ton_post() -> Any:
    try:
        _require_admin()
        init_wins_db()
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        client_id = _safe_str(data.get("client_id"))
        delta_raw = data.get("delta_ton")
        if not client_id or delta_raw is None:
            return jsonify({"ok": False, "error": "client_id and delta_ton are required"}), 400
        delta = float(str(delta_raw).replace(",", "."))
        if delta == 0:
            return jsonify({"ok": False, "error": "delta_ton must be != 0"}), 400
        idem = f"admin_ton:{int(time.time())}:{client_id}:{delta}"
        bal = apply_client_tx(client_id=client_id, amount_ton=float(delta), tx_type="admin_adjust", idempotency_key=idem, meta_json=None)
        return jsonify({"ok": True, "client_id": client_id, "balance_ton": bal})
    except Exception as e:
        return _admin_error(e)


@app.post("/api/topup/ton/create")
def api_topup_ton_create_post() -> Any:
    init_wins_db()
    _tonpay_cleanup()
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    client_id = _safe_str(data.get("client_id"))
    amount_ton = _safe_float(data.get("amount_ton"))
    if not client_id or amount_ton is None:
        return jsonify({"ok": False, "error": "client_id and amount_ton are required"}), 400
    amount_ton = float(amount_ton)

    min_amount = _tonpay_get_min_amount_ton()
    if amount_ton < min_amount:
        return jsonify({"ok": False, "error": f"min amount is {min_amount}"}), 400

    wallet = _tonpay_get_deposit_address()
    if not wallet:
        return jsonify({"ok": False, "error": "TON_DEPOSIT_ADDRESS is not set"}), 500

    now = int(time.time())
    timeout_sec = _tonpay_get_timeout_sec()
    comment = _tonpay_make_comment(user_id=client_id)
    sess = {
        "client_id": client_id,
        "amount_ton": amount_ton,
        "wallet": wallet,
        "comment": comment,
        "created_ts": now,
        "expires_ts": now + timeout_sec,
    }
    with _tonpay_lock:
        _tonpay_sessions[comment] = sess

    nano = _tonpay_ton_to_nano(amount_ton)
    body = f"{comment}".encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    tonkeeper_link = f"ton://transfer/{wallet}?amount={nano}&text={payload_b64}"

    return jsonify(
        {
            "ok": True,
            "deposit_address": wallet,
            "amount_ton": amount_ton,
            "comment": comment,
            "expires_in_sec": timeout_sec,
            "tonkeeper_link": tonkeeper_link,
        }
    )


@app.post("/api/topup/ton/check")
def api_topup_ton_check_post() -> Any:
    init_wins_db()
    _tonpay_cleanup()
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    client_id = _safe_str(data.get("client_id"))
    comment = _safe_str(data.get("comment"))
    if not client_id or not comment:
        return jsonify({"ok": False, "error": "client_id and comment are required"}), 400

    now = int(time.time())
    with _tonpay_lock:
        sess = _tonpay_sessions.get(comment)
    if not sess:
        return jsonify({"ok": True, "status": "not_found"})
    if str(sess.get("client_id") or "") != client_id:
        return jsonify({"ok": False, "error": "wrong client"}), 403
    try:
        expires_ts = int(sess.get("expires_ts") or 0)
    except Exception:
        expires_ts = 0
    if expires_ts and now > expires_ts:
        return jsonify({"ok": True, "status": "expired"})

    wallet = str(sess.get("wallet") or "").strip()
    amount_ton = float(sess.get("amount_ton") or 0.0)

    try:
        tx = _tonpay_find_matching_tx(wallet=wallet, expected_comment=comment, expected_amount_ton=amount_ton)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "tonapi failed"}), 400

    if not tx:
        left = max(0, (expires_ts - now) if expires_ts else _tonpay_get_timeout_sec())
        return jsonify({"ok": True, "status": "pending", "expires_in_sec": left})

    tx_hash = _tonpay_tx_hash(tx)
    if not tx_hash:
        return jsonify({"ok": True, "status": "pending"})

    with _tonpay_lock:
        if tx_hash in _tonpay_used_tx_hashes:
            return jsonify({"ok": True, "status": "already_used"})
        _tonpay_used_tx_hashes[tx_hash] = int(time.time())
        try:
            del _tonpay_sessions[comment]
        except Exception:
            pass

    idem = f"topup:ton:{tx_hash}"
    meta_json = _safe_json({"provider": "tonapi", "deposit_address": wallet, "comment": comment, "tx_hash": tx_hash})
    try:
        bal = apply_client_tx(client_id=client_id, amount_ton=float(amount_ton), tx_type="topup_ton", idempotency_key=idem, meta_json=meta_json)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "credit failed"}), 400

    return jsonify({"ok": True, "status": "paid", "tx_hash": tx_hash, "balance_ton": bal})



@app.post("/api/admin/promos/create")
def api_admin_promos_create_post() -> Any:
    try:
        _require_admin()
        init_wins_db()
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        amount_raw = data.get("amount_ton")
        max_uses_raw = data.get("max_uses", 1)
        code = _safe_str(data.get("code"))
        if amount_raw is None:
            return jsonify({"ok": False, "error": "amount_ton is required"}), 400
        amount = float(str(amount_raw).replace(",", "."))
        if not (amount > 0):
            return jsonify({"ok": False, "error": "amount_ton must be > 0"}), 400
        max_uses = int(max_uses_raw)
        if max_uses <= 0:
            max_uses = 1
        created = create_promo(amount_ton=amount, max_uses=max_uses, code=code)
        return jsonify({"ok": True, "code": created, "amount_ton": amount, "max_uses": max_uses})
    except Exception as e:
        return _admin_error(e)


@app.get("/health")
def health() -> Any:
    return {"ok": True}


@app.get("/api/balance")
def api_balance_get() -> Any:
    init_wins_db()
    client_id = _safe_str(request.args.get("client_id"))
    if not client_id:
        return jsonify({"ok": False, "error": "client_id is required"}), 400
    try:
        bal = get_balance_client(client_id=client_id)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "failed"}), 400
    return jsonify({"ok": True, "client_id": client_id, "balance_ton": bal})


@app.get("/api/referral")
def api_referral_get() -> Any:
    init_wins_db()
    client_id = _safe_str(request.args.get("client_id"))
    if not client_id:
        return jsonify({"ok": False, "error": "client_id is required"}), 400

    ua = request.headers.get("User-Agent")
    try:
        touch_ref_client(client_id=client_id, ip=None, user_agent=ua)
        code = get_referral_code(client_id=client_id)
        overview = get_referral_overview(client_id=client_id)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "failed"}), 400

    bot_username = "holder_maket_bot"
    start_param = f"ref_{code}" if code else ""
    link = None
    if bot_username and start_param:
        link = f"https://t.me/{bot_username}?start={start_param}"

    prefix = _safe_str(os.getenv("INVITE_STARTAPP_PREFIX")) or "roulette_inviteCode"
    invite_link = None
    invite_bot = bot_username
    if code:
        invite_link = f"https://t.me/{invite_bot}/app?startapp={prefix}{code}"

    return jsonify({"ok": True, "code": code, "link": link, "invite_link": invite_link, "overview": overview})


@app.post("/api/referral/bind")
def api_referral_bind_post() -> Any:
    init_wins_db()
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    client_id = _safe_str(data.get("client_id"))
    code = _safe_str(data.get("code"))
    if not client_id or not code:
        return jsonify({"ok": False, "error": "client_id and code are required"}), 400

    # accept ref_XXXX too
    norm = str(code).strip()
    if norm.lower().startswith("ref_"):
        norm = norm[4:]

    ua = request.headers.get("User-Agent")
    try:
        touch_ref_client(client_id=client_id, ip=None, user_agent=ua)
        bound = bind_referral(invited_client_id=client_id, inviter_code=norm, invited_ip=None, invited_user_agent=ua)
        overview = get_referral_overview(client_id=client_id)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "failed"}), 400

    return jsonify({"ok": True, "bound": bool(bound), "overview": overview})


@app.get("/api/topups")
def api_topups_get() -> Any:
    init_wins_db()
    client_id = _safe_str(request.args.get("client_id"))
    if not client_id:
        return jsonify({"ok": False, "error": "client_id is required"}), 400
    limit_raw = request.args.get("limit", "20")
    try:
        limit = max(1, min(50, int(limit_raw)))
    except Exception:
        limit = 20
    try:
        items = list_topups(client_id=client_id, limit=limit)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "failed"}), 400
    return jsonify({"ok": True, "items": items})


@app.post("/api/topup/cryptobot/create")
def api_topup_cryptobot_create() -> Any:
    init_wins_db()
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    client_id = _safe_str(data.get("client_id"))
    amount_ton = _safe_float(data.get("amount_ton"))
    promo_code = _safe_str(data.get("promo_code"))
    if not client_id or amount_ton is None:
        return jsonify({"ok": False, "error": "client_id and amount_ton are required"}), 400
    if not (amount_ton > 0):
        return jsonify({"ok": False, "error": "amount_ton must be > 0"}), 400

    amount_ton = float(amount_ton)
    idem = f"cb_create:{client_id}:{_now_ms()}:{secrets.token_hex(8)}"
    meta = {"promo_code": promo_code} if promo_code else None
    meta_json = _safe_json(meta)
    intent_id = create_payment_intent(
        provider="cryptobot",
        client_id=client_id,
        amount_ton=amount_ton,
        stars_amount=None,
        currency="TON",
        provider_invoice_id=None,
        pay_url=None,
        idempotency_key=idem,
        meta_json=meta_json,
    )

    try:
        payload = {
            "asset": "TON",
            "amount": f"{amount_ton:.2f}",
            "description": "Пополнение баланса",
            "hidden_message": "Спасибо за пополнение",
            "payload": intent_id,
            "allow_comments": False,
            "allow_anonymous": False,
        }
        res = _crypto_pay_post("createInvoice", payload)
        result = res.get("result") or {}
        invoice_id = result.get("invoice_id")
        pay_url = result.get("pay_url")
        update_payment_intent(intent_id=intent_id, provider_invoice_id=str(invoice_id) if invoice_id else None, pay_url=str(pay_url) if pay_url else None)
    except Exception as e:
        update_payment_intent(intent_id=intent_id, status="failed", meta_json=_safe_json({"error": str(e)}))
        return jsonify({"ok": False, "error": str(e) or "cryptobot failed"}), 400

    return jsonify({"ok": True, "intent_id": intent_id, "pay_url": pay_url})


@app.get("/api/topup/cryptobot/status")
def api_topup_cryptobot_status() -> Any:
    init_wins_db()
    intent_id = _safe_str(request.args.get("intent_id"))
    if not intent_id:
        return jsonify({"ok": False, "error": "intent_id is required"}), 400
    intent = get_payment_intent(intent_id=intent_id)
    if not intent:
        return jsonify({"ok": False, "error": "intent not found"}), 404
    if str(intent.get("provider") or "").lower() != "cryptobot":
        return jsonify({"ok": False, "error": "wrong provider"}), 400

    status = str(intent.get("status") or "created")
    invoice_id = intent.get("provider_invoice_id")
    if status == "paid":
        bal = get_balance_client(client_id=str(intent.get("client_id")))
        return jsonify({"ok": True, "intent": intent, "balance_ton": bal})
    if not invoice_id:
        return jsonify({"ok": True, "intent": intent})

    try:
        res = _crypto_pay_post("getInvoices", {"invoice_ids": str(invoice_id)})
        invs = (res.get("result") or {}).get("items") or []
        inv = invs[0] if invs else {}
        inv_status = str(inv.get("status") or "")
        if inv_status:
            update_payment_intent(intent_id=intent_id, status=inv_status)
        if inv_status == "paid":
            amount_ton = float(intent.get("amount_ton") or 0.0)
            cid = str(intent.get("client_id") or "")
            idem = f"topup:cryptobot:{intent_id}"
            meta_json = _safe_json({"intent_id": intent_id, "provider": "cryptobot", "invoice": inv})
            apply_client_tx(client_id=cid, amount_ton=amount_ton, tx_type="topup", idempotency_key=idem, meta_json=meta_json)
            mark_payment_intent_paid(intent_id=intent_id, paid_meta_json=meta_json)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "status failed"}), 400

    intent2 = get_payment_intent(intent_id=intent_id) or intent
    bal2 = get_balance_client(client_id=str(intent2.get("client_id")))
    return jsonify({"ok": True, "intent": intent2, "balance_ton": bal2})


@app.post("/api/topup/stars/create")
def api_topup_stars_create() -> Any:
    init_wins_db()
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    client_id = _safe_str(data.get("client_id"))
    stars_amount_raw = data.get("stars_amount")
    if not client_id or stars_amount_raw is None:
        return jsonify({"ok": False, "error": "client_id and stars_amount are required"}), 400
    try:
        stars_amount = int(stars_amount_raw)
    except Exception:
        return jsonify({"ok": False, "error": "stars_amount must be int"}), 400
    if stars_amount <= 0:
        return jsonify({"ok": False, "error": "stars_amount must be > 0"}), 400

    rate = int(os.getenv("STARS_PER_TON", "100"))
    if rate <= 0:
        rate = 100
    amount_ton = float(stars_amount) / float(rate)

    idem = f"stars_create:{client_id}:{_now_ms()}:{secrets.token_hex(8)}"
    intent_id = create_payment_intent(
        provider="stars",
        client_id=client_id,
        amount_ton=amount_ton,
        stars_amount=stars_amount,
        currency="XTR",
        provider_invoice_id=None,
        pay_url=None,
        idempotency_key=idem,
        meta_json=_safe_json({"rate": rate}),
    )

    title = "Пополнение баланса"
    description = f"Пополнение на {stars_amount} звёзд"
    prices = [{"label": "Stars", "amount": int(stars_amount)}]

    try:
        provider_token = os.getenv("TELEGRAM_PROVIDER_TOKEN", "")
        inv = _tg_api_post(
            "createInvoiceLink",
            {
                "title": title,
                "description": description,
                "payload": intent_id,
                "provider_token": provider_token,
                "currency": "XTR",
                "prices": prices,
            },
        )
        invoice_link = (inv.get("result") or "")
        update_payment_intent(intent_id=intent_id, pay_url=str(invoice_link) if invoice_link else None)
    except Exception as e:
        update_payment_intent(intent_id=intent_id, status="failed", meta_json=_safe_json({"error": str(e)}))
        return jsonify({"ok": False, "error": str(e) or "invoice failed"}), 400

    return jsonify(
        {
            "ok": True,
            "intent_id": intent_id,
            "invoice_link": invoice_link,
            "stars_amount": stars_amount,
            "amount_ton": amount_ton,
            "rate": rate,
        }
    )


@app.post("/api/topup/stars/confirm")
def api_topup_stars_confirm() -> Any:
    init_wins_db()
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    secret = _safe_str(data.get("secret"))
    expected = _safe_str(os.getenv("TOPUP_CONFIRM_SECRET"))
    if expected and secret != expected:
        return jsonify({"ok": False, "error": "forbidden"}), 403

    intent_id = _safe_str(data.get("intent_id"))
    tg_charge_id = _safe_str(data.get("telegram_payment_charge_id"))
    provider_charge_id = _safe_str(data.get("provider_payment_charge_id"))
    if not intent_id:
        return jsonify({"ok": False, "error": "intent_id is required"}), 400

    if tg_charge_id:
        existing = get_payment_intent_by_tg_charge(telegram_payment_charge_id=tg_charge_id)
        if existing and str(existing.get("intent_id") or "") != str(intent_id):
            return jsonify({"ok": False, "error": "duplicate telegram_payment_charge_id"}), 409
    intent = get_payment_intent(intent_id=intent_id)
    if not intent:
        return jsonify({"ok": False, "error": "intent not found"}), 404
    if str(intent.get("provider") or "").lower() != "stars":
        return jsonify({"ok": False, "error": "wrong provider"}), 400

    if str(intent.get("status") or "") == "paid":
        bal = get_balance_client(client_id=str(intent.get("client_id")))
        return jsonify({"ok": True, "intent": intent, "balance_ton": bal})

    try:
        update_payment_intent(
            intent_id=intent_id,
            telegram_payment_charge_id=tg_charge_id,
            provider_payment_charge_id=provider_charge_id,
        )
    except Exception:
        pass

    amount_ton = float(intent.get("amount_ton") or 0.0)
    cid = str(intent.get("client_id") or "")
    idem = f"topup:stars:{intent_id}"
    meta_json = _safe_json(
        {
            "intent_id": intent_id,
            "provider": "stars",
            "telegram_payment_charge_id": tg_charge_id,
            "provider_payment_charge_id": provider_charge_id,
        }
    )

    try:
        apply_client_tx(client_id=cid, amount_ton=amount_ton, tx_type="topup", idempotency_key=idem, meta_json=meta_json)
        mark_payment_intent_paid(intent_id=intent_id, paid_meta_json=meta_json)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "confirm failed"}), 400

    intent2 = get_payment_intent(intent_id=intent_id) or intent
    bal2 = get_balance_client(client_id=cid)
    return jsonify({"ok": True, "intent": intent2, "balance_ton": bal2})


@app.get("/api/coin-balance")
def api_coin_balance_get() -> Any:
    init_wins_db()
    client_id = _safe_str(request.args.get("client_id"))
    if not client_id:
        return jsonify({"ok": False, "error": "client_id is required"}), 400
    try:
        bal = get_coin_balance_client(client_id=client_id)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "failed"}), 400
    return jsonify({"ok": True, "client_id": client_id, "balance_coins": int(bal)})


@app.post("/api/coin-tx")
def api_coin_tx_post() -> Any:
    init_wins_db()
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    client_id = _safe_str(data.get("client_id"))
    amount_coins = data.get("amount_coins")
    tx_type = _safe_str(data.get("tx_type")) or "unknown"
    idempotency_key = _safe_str(data.get("idempotency_key"))
    meta_json = _safe_json(data.get("meta"))
    if not client_id or amount_coins is None or not idempotency_key:
        return (
            jsonify({"ok": False, "error": "client_id, amount_coins and idempotency_key are required"}),
            400,
        )
    try:
        bal = apply_coin_tx(
            client_id=client_id,
            amount_coins=int(amount_coins),
            tx_type=tx_type,
            idempotency_key=idempotency_key,
            meta_json=meta_json,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "tx failed"}), 400
    return jsonify({"ok": True, "client_id": client_id, "balance_coins": int(bal)})


@app.post("/api/tx")
def api_tx_post() -> Any:
    init_wins_db()
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    client_id = _safe_str(data.get("client_id"))
    amount_ton = _safe_float(data.get("amount_ton"))
    tx_type = _safe_str(data.get("tx_type")) or "unknown"
    idempotency_key = _safe_str(data.get("idempotency_key"))
    meta_json = _safe_json(data.get("meta"))
    if not client_id or amount_ton is None or not idempotency_key:
        return (
            jsonify({"ok": False, "error": "client_id, amount_ton and idempotency_key are required"}),
            400,
        )
    try:
        bal = apply_client_tx(
            client_id=client_id,
            amount_ton=float(amount_ton),
            tx_type=tx_type,
            idempotency_key=idempotency_key,
            meta_json=meta_json,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "tx failed"}), 400
    return jsonify({"ok": True, "client_id": client_id, "balance_ton": bal})


@app.post("/api/wins")
def api_wins_post() -> Any:
    init_wins_db()
    data: Dict[str, Any] = request.get_json(silent=True) or {}

    ts = data.get("ts")
    player_name = _safe_str(data.get("player_name"))
    win_type = _safe_str(data.get("win_type")) or "unknown"
    game = _safe_str(data.get("game"))
    gift_id = _safe_str(data.get("gift_id"))
    gift_name = _safe_str(data.get("gift_name"))
    amount_ton = _safe_float(data.get("amount_ton"))

    meta = data.get("meta")
    meta_json = None
    if meta is not None:
        try:
            meta_json = json.dumps(meta, ensure_ascii=False)
        except Exception:
            meta_json = None

    row_id = insert_win(
        ts=ts,
        player_name=player_name,
        win_type=win_type,
        game=game,
        gift_id=gift_id,
        gift_name=gift_name,
        amount_ton=amount_ton,
        meta_json=meta_json,
    )
    return jsonify({"ok": True, "id": row_id})


@app.get("/api/wins")
def api_wins_get() -> Any:
    init_wins_db()
    limit_raw = request.args.get("limit", "50")
    try:
        limit = max(1, min(200, int(limit_raw)))
    except Exception:
        limit = 50
    return jsonify({"ok": True, "items": list_wins(limit=limit)})


@app.get("/api/cases")
def api_cases_get() -> Any:
    init_wins_db()
    include_disabled_raw = request.args.get("include_disabled")
    include_disabled = bool(include_disabled_raw) and str(include_disabled_raw) not in ("0", "false", "False")
    try:
        items = list_cases(include_disabled=include_disabled)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "failed"}), 400
    return jsonify({"ok": True, "items": items})


@app.post("/api/cases/open")
def api_cases_open_post() -> Any:
    init_wins_db()
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    client_id = _safe_str(data.get("client_id"))
    case_id = _safe_str(data.get("case_id"))
    mult_raw = data.get("mult", 1)
    demo_raw = data.get("demo")
    demo = bool(demo_raw)
    if demo_raw in (0, "0", "false", "False", False, None):
        demo = False
    idempotency_key = _safe_str(data.get("idempotency_key"))
    meta_json = _safe_json(data.get("meta"))
    try:
        mult = int(mult_raw)
    except Exception:
        mult = 1
    mult = max(1, min(5, mult))

    if not client_id or not case_id or not idempotency_key:
        return (
            jsonify({"ok": False, "error": "client_id, case_id and idempotency_key are required"}),
            400,
        )
    try:
        if mult <= 1:
            res = open_case(
                client_id=client_id,
                case_id=case_id,
                demo=demo,
                idempotency_key=idempotency_key,
                meta_json=meta_json,
                mult=1,
            )
        else:
            res = open_case(
                client_id=client_id,
                case_id=case_id,
                demo=demo,
                idempotency_key=idempotency_key,
                meta_json=meta_json,
                mult=mult,
            )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "open failed"}), 400
    return jsonify({"ok": True, "result": res})


@app.get("/api/crash/state")
def api_crash_state_get() -> Any:
    init_wins_db()
    eng = get_crash_engine()
    return jsonify({"ok": True, "state": eng.get_public_state()})


@app.post("/api/crash/bet")
def api_crash_bet_post() -> Any:
    init_wins_db()
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    client_id = _safe_str(data.get("client_id"))
    amount_ton = _safe_float(data.get("amount_ton"))
    idempotency_key = _safe_str(data.get("idempotency_key"))
    meta_json = _safe_json(data.get("meta"))
    if not client_id or not idempotency_key or amount_ton is None:
        return jsonify({"ok": False, "error": "client_id, amount_ton and idempotency_key are required"}), 400

    amount_coins = ton_to_coins(float(amount_ton))
    if amount_coins <= 0:
        return jsonify({"ok": False, "error": "amount_ton must be > 0"}), 400

    eng = get_crash_engine()
    try:
        # debit first (idempotent)
        apply_coin_tx(
            client_id=client_id,
            amount_coins=-int(amount_coins),
            tx_type="crash_bet",
            idempotency_key=f"{idempotency_key}:bet",
            meta_json=meta_json,
        )
        eng.bankroll_on_bet(amount_coins=int(amount_coins))

        placed = eng.place_bet(client_id=client_id, amount_coins=int(amount_coins), idempotency_key=f"{idempotency_key}:eng")
        bal = get_coin_balance_client(client_id=client_id)
        return jsonify({"ok": True, "result": placed, "balance_coins": int(bal)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "bet failed"}), 400


@app.post("/api/crash/cashout")
def api_crash_cashout_post() -> Any:
    init_wins_db()
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    client_id = _safe_str(data.get("client_id"))
    idempotency_key = _safe_str(data.get("idempotency_key"))
    meta_json = _safe_json(data.get("meta"))
    if not client_id or not idempotency_key:
        return jsonify({"ok": False, "error": "client_id and idempotency_key are required"}), 400

    eng = get_crash_engine()
    try:
        res = eng.cashout(client_id=client_id, idempotency_key=f"{idempotency_key}:eng")
        payout = int(res.get("payout_coins") or 0)
        if payout > 0:
            apply_coin_tx(
                client_id=client_id,
                amount_coins=int(payout),
                tx_type="crash_cashout",
                idempotency_key=f"{idempotency_key}:payout",
                meta_json=meta_json,
            )
            eng.bankroll_on_payout(payout_coins=int(payout))
        bal = get_coin_balance_client(client_id=client_id)
        return jsonify({"ok": True, "result": res, "balance_coins": int(bal)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "cashout failed"}), 400


@app.get("/api/game-flags")
def api_game_flags_get() -> Any:
    init_wins_db()
    return jsonify({"ok": True, "flags": get_game_flags()})


@app.post("/api/game-flags")
def api_game_flags_post() -> Any:
    init_wins_db()
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    game = _safe_str(data.get("game"))
    enabled_raw = data.get("enabled")
    enabled = bool(enabled_raw)
    if enabled_raw in (0, "0", "false", "False", False, None):
        enabled = False
    if not game:
        return jsonify({"ok": False, "error": "game is required"}), 400
    set_game_enabled(game=game, enabled=enabled)
    return jsonify({"ok": True, "flags": get_game_flags()})


@app.get("/api/promos")
def api_promos_get() -> Any:
    init_wins_db()
    limit_raw = request.args.get("limit", "50")
    try:
        limit = max(1, min(200, int(limit_raw)))
    except Exception:
        limit = 50
    return jsonify({"ok": True, "items": list_active_promos(limit=limit)})


@app.post("/api/notify/contest-buy")
def api_notify_contest_buy_post() -> Any:
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    tg_user_id_raw = data.get("tg_user_id")
    contest_title = _safe_str(data.get("contest_title")) or "Конкурс"
    qty_raw = data.get("qty")
    total_ton = _safe_float(data.get("total_ton"))
    ts_ms_raw = data.get("ts_ms")

    try:
        tg_user_id = int(tg_user_id_raw)
    except Exception:
        tg_user_id = 0

    try:
        qty = int(qty_raw)
    except Exception:
        qty = 0

    try:
        ts_ms = int(ts_ms_raw)
    except Exception:
        ts_ms = _now_ms()

    if tg_user_id <= 0:
        return jsonify({"ok": False, "error": "tg_user_id is required"}), 400
    if qty <= 0:
        return jsonify({"ok": False, "error": "qty must be > 0"}), 400
    if total_ton is None or not (total_ton > 0):
        return jsonify({"ok": False, "error": "total_ton must be > 0"}), 400

    try:
        dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts_ms) // 1000))
    except Exception:
        dt = str(ts_ms)

    text = (
        f"✅ Покупка билетов\n"
        f"Конкурс: {contest_title}\n"
        f"Билеты: {qty}\n"
        f"Сумма: {float(total_ton):.2f} TON\n"
        f"Время: {dt}"
    )

    try:
        _tg_api_post(
            "sendMessage",
            {
                "chat_id": tg_user_id,
                "text": text,
                "disable_web_page_preview": True,
            },
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "telegram failed"}), 400
    init_wins_db()
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    code = _safe_str(data.get("code"))
    client_id = _safe_str(data.get("client_id"))
    if not code or not client_id:
        return jsonify({"ok": False, "error": "code and client_id are required"}), 400
    try:
        amount = redeem_promo_client(code=code, client_id=client_id)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or "redeem failed"}), 400
    try:
        coins = get_coin_balance_client(client_id=client_id)
    except Exception:
        coins = None
    return jsonify({"ok": True, "amount_ton": amount, "balance_coins": coins})


def main() -> None:
    host = os.getenv("WINS_API_HOST", "0.0.0.0")
    port_raw = os.getenv("PORT") or os.getenv("WINS_API_PORT") or "8080"
    port = int(port_raw)
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()
