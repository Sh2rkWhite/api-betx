import os
import random
import sqlite3
import time
from typing import Any, Dict, List, Optional

import secrets

from game_config import cfg


_DEFAULT_WINS_DB_PATH = os.path.join(os.path.dirname(__file__), "wins.sqlite3")
WINS_DB_PATH = os.getenv("WINS_DB_PATH", _DEFAULT_WINS_DB_PATH)


COIN_SCALE_TON = int(os.getenv("COIN_SCALE_TON", "1000"))


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(WINS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_wins_db() -> None:
    conn = get_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wins (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts INTEGER NOT NULL,
              player_name TEXT,
              win_type TEXT NOT NULL,
              game TEXT,
              gift_id TEXT,
              gift_name TEXT,
              amount_ton REAL,
              meta_json TEXT
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS referral_clients (
              client_id TEXT PRIMARY KEY,
              created_ts INTEGER NOT NULL,
              last_seen_ts INTEGER NOT NULL,
              last_ip TEXT,
              last_user_agent TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ref_clients_seen ON referral_clients(last_seen_ts DESC)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS referral_codes (
              code TEXT PRIMARY KEY,
              inviter_client_id TEXT NOT NULL,
              created_ts INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ref_codes_inviter ON referral_codes(inviter_client_id)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS referrals (
              invited_client_id TEXT PRIMARY KEY,
              inviter_client_id TEXT NOT NULL,
              code TEXT NOT NULL,
              created_ts INTEGER NOT NULL,
              status TEXT NOT NULL,
              fraud_reason TEXT,
              invited_ip TEXT,
              invited_user_agent TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_inviter_ts ON referrals(inviter_client_id, created_ts DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_status ON referrals(status)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS referral_wallet (
              client_id TEXT PRIMARY KEY,
              available_cents INTEGER NOT NULL,
              locked_cents INTEGER NOT NULL,
              updated_ts INTEGER NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS referral_rewards (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_ts INTEGER NOT NULL,
              beneficiary_client_id TEXT NOT NULL,
              invited_client_id TEXT,
              reward_type TEXT NOT NULL,
              amount_cents INTEGER NOT NULL,
              status TEXT NOT NULL,
              available_ts INTEGER,
              source_event_id TEXT,
              source_amount_cents INTEGER,
              reversed_ts INTEGER,
              reverse_reason TEXT
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_ref_rewards_unique_event ON referral_rewards(beneficiary_client_id, reward_type, source_event_id) WHERE source_event_id IS NOT NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ref_rewards_benef_status ON referral_rewards(beneficiary_client_id, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ref_rewards_available_ts ON referral_rewards(available_ts)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS game_flags (
              game TEXT PRIMARY KEY,
              enabled INTEGER NOT NULL,
              updated_ts INTEGER NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS promo_codes (
              code TEXT PRIMARY KEY,
              amount_ton REAL NOT NULL,
              max_uses INTEGER NOT NULL,
              used_count INTEGER NOT NULL,
              active INTEGER NOT NULL,
              created_ts INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS promo_redemptions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              code TEXT NOT NULL,
              user_id INTEGER NOT NULL,
              ts INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_promo_redemptions_code_user ON promo_redemptions(code, user_id)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS promo_redemptions_clients (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              code TEXT NOT NULL,
              client_id TEXT NOT NULL,
              ts INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_promo_redemptions_code_client ON promo_redemptions_clients(code, client_id)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet (
              user_id INTEGER PRIMARY KEY,
              ton_balance REAL NOT NULL,
              updated_ts INTEGER NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_clients (
              client_id TEXT PRIMARY KEY,
              ton_balance_cents INTEGER NOT NULL,
              updated_ts INTEGER NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gift_inventory (
              client_id TEXT NOT NULL,
              gift_id TEXT NOT NULL,
              qty INTEGER NOT NULL,
              updated_ts INTEGER NOT NULL,
              PRIMARY KEY (client_id, gift_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gift_inventory_client ON gift_inventory(client_id)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gift_transactions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts INTEGER NOT NULL,
              client_id TEXT NOT NULL,
              gift_id TEXT NOT NULL,
              qty_delta INTEGER NOT NULL,
              tx_type TEXT NOT NULL,
              idempotency_key TEXT NOT NULL,
              meta_json TEXT
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_gift_tx_idem ON gift_transactions(idempotency_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gift_tx_client_ts ON gift_transactions(client_id, ts DESC)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS coin_wallet_clients (
              client_id TEXT PRIMARY KEY,
              balance_coins INTEGER NOT NULL,
              updated_ts INTEGER NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS coin_transactions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts INTEGER NOT NULL,
              client_id TEXT NOT NULL,
              amount_coins INTEGER NOT NULL,
              tx_type TEXT NOT NULL,
              idempotency_key TEXT NOT NULL,
              meta_json TEXT
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_coin_tx_idem ON coin_transactions(idempotency_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_coin_tx_client_ts ON coin_transactions(client_id, ts DESC)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS player_state (
              client_id TEXT PRIMARY KEY,
              loss_streak INTEGER NOT NULL,
              updated_ts INTEGER NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cases (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              price_coins INTEGER NOT NULL,
              theme TEXT,
              enabled INTEGER NOT NULL,
              sort_order INTEGER NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS case_drops (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              case_id TEXT NOT NULL,
              reward_type TEXT NOT NULL,
              reward_id TEXT,
              payout_coins INTEGER,
              weight REAL NOT NULL,
              tier TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_case_drops_case ON case_drops(case_id)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_ts INTEGER NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_sessions (
              token TEXT PRIMARY KEY,
              chat_id INTEGER,
              created_ts INTEGER NOT NULL,
              expires_ts INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_admin_sessions_expires ON admin_sessions(expires_ts)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contests (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              prize_gift_id TEXT,
              ticket_price_ton REAL NOT NULL,
              ends_ts INTEGER NOT NULL,
              enabled INTEGER NOT NULL,
              status TEXT,
              winner_tg_user_id INTEGER,
              winner_name TEXT,
              completed_ts INTEGER,
              created_ts INTEGER NOT NULL,
              updated_ts INTEGER NOT NULL
            )
            """
        )
        try:
            conn.execute("ALTER TABLE contests ADD COLUMN prize_gift_id TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE contests ADD COLUMN status TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE contests ADD COLUMN winner_tg_user_id INTEGER")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE contests ADD COLUMN winner_name TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE contests ADD COLUMN completed_ts INTEGER")
        except Exception:
            pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_contests_enabled ON contests(enabled)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_contests_ends_ts ON contests(ends_ts)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contest_tickets (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              contest_id TEXT NOT NULL,
              tg_user_id INTEGER NOT NULL,
              tg_username TEXT,
              tg_first_name TEXT,
              tg_last_name TEXT,
              qty INTEGER NOT NULL,
              ts INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_contest_tickets_contest ON contest_tickets(contest_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_contest_tickets_user ON contest_tickets(tg_user_id)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS client_transactions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts INTEGER NOT NULL,
              client_id TEXT NOT NULL,
              amount_cents INTEGER NOT NULL,
              tx_type TEXT NOT NULL,
              idempotency_key TEXT NOT NULL,
              meta_json TEXT
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_client_tx_idem ON client_transactions(idempotency_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_client_tx_client_ts ON client_transactions(client_id, ts DESC)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_intents (
              intent_id TEXT PRIMARY KEY,
              provider TEXT NOT NULL,
              client_id TEXT NOT NULL,
              amount_ton REAL NOT NULL,
              stars_amount INTEGER,
              currency TEXT NOT NULL,
              status TEXT NOT NULL,
              provider_invoice_id TEXT,
              pay_url TEXT,
              idempotency_key TEXT NOT NULL,
              meta_json TEXT,
              paid_meta_json TEXT,
              telegram_payment_charge_id TEXT,
              provider_payment_charge_id TEXT,
              created_ts INTEGER NOT NULL,
              updated_ts INTEGER NOT NULL,
              paid_ts INTEGER
            )
            """
        )

        try:
            conn.execute("ALTER TABLE payment_intents ADD COLUMN telegram_payment_charge_id TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE payment_intents ADD COLUMN provider_payment_charge_id TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE payment_intents ADD COLUMN paid_ts INTEGER")
        except Exception:
            pass
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_intents_idem ON payment_intents(idempotency_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_payment_intents_client_ts ON payment_intents(client_id, created_ts DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_payment_intents_provider_invoice ON payment_intents(provider, provider_invoice_id)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_intents_tg_charge ON payment_intents(telegram_payment_charge_id) WHERE telegram_payment_charge_id IS NOT NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_payment_intents_provider_charge ON payment_intents(provider_payment_charge_id) WHERE provider_payment_charge_id IS NOT NULL"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS support_sessions (
              user_id INTEGER PRIMARY KEY,
              user_chat_id INTEGER NOT NULL,
              user_username TEXT,
              created_ts INTEGER NOT NULL,
              status TEXT NOT NULL,
              admin_chat_id INTEGER,
              closed_ts INTEGER
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_support_sessions_status ON support_sessions(status)")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_wins_ts ON wins(ts DESC)")
        conn.commit()
    finally:
        conn.close()


def create_admin_session(*, chat_id: Optional[int], ttl_seconds: int = 60 * 15) -> str:
    init_wins_db()
    ttl = int(ttl_seconds)
    if ttl <= 0:
        ttl = 60 * 15
    token = secrets.token_urlsafe(16)
    now = int(time.time())
    expires_ts = now + ttl
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO admin_sessions (token, chat_id, created_ts, expires_ts)
            VALUES (?, ?, ?, ?)
            """,
            (
                token,
                int(chat_id) if chat_id is not None else None,
                now,
                int(expires_ts),
            ),
        )
        conn.commit()
        return token
    finally:
        conn.close()


def validate_admin_session(*, token: str) -> Optional[Dict[str, Any]]:
    init_wins_db()
    tok = str(token or "").strip()
    if not tok:
        return None
    now = int(time.time())
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT token, chat_id, created_ts, expires_ts
            FROM admin_sessions
            WHERE token = ?
            """,
            (tok,),
        ).fetchone()
        if not row:
            return None
        if int(row["expires_ts"] or 0) < now:
            try:
                conn.execute("DELETE FROM admin_sessions WHERE token = ?", (tok,))
                conn.commit()
            except Exception:
                pass
            return None
        return dict(row)
    finally:
        conn.close()


def revoke_admin_session(*, token: str) -> None:
    init_wins_db()
    tok = str(token or "").strip()
    if not tok:
        return
    conn = get_conn()
    try:
        conn.execute("DELETE FROM admin_sessions WHERE token = ?", (tok,))
        conn.commit()
    finally:
        conn.close()


def admin_list_contests(*, include_disabled: bool = True) -> List[Dict[str, Any]]:
    init_wins_db()
    conn = get_conn()
    try:
        if include_disabled:
            rows = conn.execute(
                """
                SELECT id, title, prize_gift_id, ticket_price_ton, ends_ts, enabled, status, winner_tg_user_id, winner_name, completed_ts, created_ts, updated_ts
                FROM contests
                ORDER BY created_ts DESC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, title, prize_gift_id, ticket_price_ton, ends_ts, enabled, status, winner_tg_user_id, winner_name, completed_ts, created_ts, updated_ts
                FROM contests
                WHERE enabled = 1
                ORDER BY created_ts DESC
                """
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_gift_inventory_client(*, client_id: str) -> List[Dict[str, Any]]:
    init_wins_db()
    cid = _normalize_client_id(client_id)
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT gift_id, qty, updated_ts
            FROM gift_inventory
            WHERE client_id = ?
              AND qty > 0
            ORDER BY updated_ts DESC
            """,
            (cid,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def apply_gift_tx(
    *,
    client_id: str,
    gift_id: str,
    qty_delta: int,
    tx_type: str,
    idempotency_key: str,
    meta_json: Optional[str] = None,
) -> int:
    init_wins_db()
    cid = _normalize_client_id(client_id)
    gid = str(gift_id or "").strip()
    if not gid:
        raise ValueError("gift_id is required")
    delta = int(qty_delta)
    if delta == 0:
        raise ValueError("qty_delta must be non-zero")
    tx_type_norm = str(tx_type or "").strip().lower() or "unknown"
    idem = str(idempotency_key or "").strip()
    if not idem:
        raise ValueError("idempotency_key is required")

    conn = get_conn()
    try:
        now = int(time.time())
        conn.execute("BEGIN IMMEDIATE")

        try:
            mult_norm = int(mult)
        except Exception:
            mult_norm = 1
        mult_norm = max(1, min(5, int(mult_norm)))

        existing = conn.execute(
            "SELECT 1 FROM gift_transactions WHERE idempotency_key = ?",
            (idem,),
        ).fetchone()
        if existing:
            row = conn.execute(
                "SELECT qty FROM gift_inventory WHERE client_id = ? AND gift_id = ?",
                (cid, gid),
            ).fetchone()
            return int(row["qty"] or 0) if row else 0

        row = conn.execute(
            "SELECT qty FROM gift_inventory WHERE client_id = ? AND gift_id = ?",
            (cid, gid),
        ).fetchone()
        current = int(row["qty"] or 0) if row else 0
        new_qty = current + int(delta)
        if new_qty < 0:
            raise ValueError("Недостаточно подарков")

        conn.execute(
            """
            INSERT INTO gift_inventory (client_id, gift_id, qty, updated_ts)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(client_id, gift_id) DO UPDATE SET
              qty = excluded.qty,
              updated_ts = excluded.updated_ts
            """,
            (cid, gid, int(new_qty), now),
        )

        conn.execute(
            """
            INSERT INTO gift_transactions (ts, client_id, gift_id, qty_delta, tx_type, idempotency_key, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (now, cid, gid, int(delta), tx_type_norm, idem, meta_json),
        )

        conn.commit()
        return int(new_qty)
    finally:
        conn.close()


def create_support_request(*, user_id: int, user_chat_id: int, user_username: Optional[str] = None) -> Dict[str, Any]:
    init_wins_db()
    uid = int(user_id)
    chat_id = int(user_chat_id)
    username = str(user_username or "").strip() or None
    conn = get_conn()
    try:
        now = int(time.time())
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO support_sessions (user_id, user_chat_id, user_username, created_ts, status, admin_chat_id, closed_ts)
            VALUES (?, ?, ?, ?, 'open', NULL, NULL)
            ON CONFLICT(user_id) DO UPDATE SET
              user_chat_id = excluded.user_chat_id,
              user_username = COALESCE(excluded.user_username, support_sessions.user_username),
              created_ts = excluded.created_ts,
              status = 'open',
              admin_chat_id = NULL,
              closed_ts = NULL
            """,
            (uid, chat_id, username, now),
        )
        conn.commit()
        return {
            "user_id": uid,
            "user_chat_id": chat_id,
            "user_username": username,
            "created_ts": now,
            "status": "open",
        }
    finally:
        conn.close()


def assign_support_session(*, user_id: int, admin_chat_id: int) -> bool:
    init_wins_db()
    uid = int(user_id)
    aid = int(admin_chat_id)
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status FROM support_sessions WHERE user_id = ?",
            (uid,),
        ).fetchone()
        if not row:
            conn.commit()
            return False
        if str(row["status"] or "") == "closed":
            conn.commit()
            return False
        conn.execute(
            "UPDATE support_sessions SET status='active', admin_chat_id=?, closed_ts=NULL WHERE user_id=?",
            (aid, uid),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def close_support_session(*, user_id: int) -> None:
    init_wins_db()
    uid = int(user_id)
    conn = get_conn()
    try:
        now = int(time.time())
        conn.execute(
            "UPDATE support_sessions SET status='closed', closed_ts=? WHERE user_id=?",
            (now, uid),
        )
        conn.commit()
    finally:
        conn.close()


def get_support_session_by_user(*, user_id: int) -> Optional[Dict[str, Any]]:
    init_wins_db()
    uid = int(user_id)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT user_id, user_chat_id, user_username, created_ts, status, admin_chat_id, closed_ts FROM support_sessions WHERE user_id = ?",
            (uid,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def admin_list_ton_transactions(*, limit: int = 50, client_id: Optional[str] = None) -> List[Dict[str, Any]]:
    init_wins_db()
    conn = get_conn()
    try:
        if client_id:
            cid = _normalize_client_id(client_id)
            rows = conn.execute(
                """
                SELECT id, ts, client_id, amount_cents, tx_type, idempotency_key, meta_json
                FROM client_transactions
                WHERE client_id = ?
                ORDER BY ts DESC, id DESC
                LIMIT ?
                """,
                (cid, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, ts, client_id, amount_cents, tx_type, idempotency_key, meta_json
                FROM client_transactions
                ORDER BY ts DESC, id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def admin_list_gift_transactions(*, limit: int = 50, client_id: Optional[str] = None) -> List[Dict[str, Any]]:
    init_wins_db()
    conn = get_conn()
    try:
        if client_id:
            cid = _normalize_client_id(client_id)
            rows = conn.execute(
                """
                SELECT id, ts, client_id, gift_id, qty_delta, tx_type, idempotency_key, meta_json
                FROM gift_transactions
                WHERE client_id = ?
                ORDER BY ts DESC, id DESC
                LIMIT ?
                """,
                (cid, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, ts, client_id, gift_id, qty_delta, tx_type, idempotency_key, meta_json
                FROM gift_transactions
                ORDER BY ts DESC, id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_support_session_by_admin(*, admin_chat_id: int) -> Optional[Dict[str, Any]]:
    init_wins_db()
    aid = int(admin_chat_id)
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT user_id, user_chat_id, user_username, created_ts, status, admin_chat_id, closed_ts
            FROM support_sessions
            WHERE admin_chat_id = ? AND status = 'active'
            ORDER BY created_ts DESC
            LIMIT 1
            """,
            (aid,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def admin_create_contest(
    *,
    title: str,
    ticket_price_ton: float,
    ends_ts: int,
    enabled: bool = True,
    prize_gift_id: Optional[str] = None,
) -> str:
    init_wins_db()
    t = str(title or "").strip()
    if not t:
        raise ValueError("title is required")
    pgid = str(prize_gift_id).strip() if prize_gift_id is not None else None
    if pgid == "":
        pgid = None
    price = float(ticket_price_ton)
    if not (price > 0):
        raise ValueError("ticket_price_ton must be > 0")
    ends = int(ends_ts)
    if ends <= int(time.time()):
        raise ValueError("ends_ts must be in future")

    cid = secrets.token_urlsafe(8)
    now = int(time.time())
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO contests (id, title, prize_gift_id, ticket_price_ton, ends_ts, enabled, status, winner_tg_user_id, winner_name, completed_ts, created_ts, updated_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (cid, t, pgid, float(price), int(ends), 1 if enabled else 0, "active", None, None, None, now, now),
        )
        conn.commit()
        return cid
    finally:
        conn.close()


def admin_update_contest(
    *,
    contest_id: str,
    title: Optional[str] = None,
    prize_gift_id: Optional[str] = None,
    ticket_price_ton: Optional[float] = None,
    ends_ts: Optional[int] = None,
    enabled: Optional[bool] = None,
) -> None:
    init_wins_db()
    cid = str(contest_id or "").strip()
    if not cid:
        raise ValueError("contest_id is required")

    fields: List[str] = []
    vals: List[Any] = []
    if title is not None:
        t = str(title or "").strip()
        if not t:
            raise ValueError("title must be non-empty")
        fields.append("title = ?")
        vals.append(t)
    if prize_gift_id is not None:
        pgid = str(prize_gift_id).strip() if prize_gift_id else ""
        fields.append("prize_gift_id = ?")
        vals.append(pgid or None)
    if ticket_price_ton is not None:
        price = float(ticket_price_ton)
        if not (price > 0):
            raise ValueError("ticket_price_ton must be > 0")
        fields.append("ticket_price_ton = ?")
        vals.append(float(price))
    if ends_ts is not None:
        ends = int(ends_ts)
        if ends <= int(time.time()):
            raise ValueError("ends_ts must be in future")
        fields.append("ends_ts = ?")
        vals.append(int(ends))
    if enabled is not None:
        fields.append("enabled = ?")
        vals.append(1 if enabled else 0)
    if not fields:
        return
    fields.append("updated_ts = ?")
    vals.append(int(time.time()))
    vals.append(cid)

    conn = get_conn()
    try:
        row = conn.execute("SELECT 1 FROM contests WHERE id = ?", (cid,)).fetchone()
        if not row:
            raise ValueError("Конкурс не найден")
        conn.execute(f"UPDATE contests SET {', '.join(fields)} WHERE id = ?", tuple(vals))
        conn.commit()
    finally:
        conn.close()


def admin_delete_contest(*, contest_id: str) -> None:
    init_wins_db()
    cid = str(contest_id or "").strip()
    if not cid:
        raise ValueError("contest_id is required")
    conn = get_conn()
    try:
        conn.execute("DELETE FROM contests WHERE id = ?", (cid,))
        conn.commit()
    finally:
        conn.close()


def contests_buy_tickets(
    *,
    contest_id: str,
    tg_user_id: int,
    qty: int,
    tg_username: Optional[str] = None,
    tg_first_name: Optional[str] = None,
    tg_last_name: Optional[str] = None,
) -> Dict[str, Any]:
    init_wins_db()
    cid = str(contest_id or "").strip()
    if not cid:
        raise ValueError("contest_id is required")
    uid = int(tg_user_id)
    if uid <= 0:
        raise ValueError("tg_user_id is required")
    q = int(qty)
    if q <= 0:
        raise ValueError("qty must be > 0")

    now = int(time.time())
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id, enabled, ends_ts, COALESCE(status, 'active') AS status
            FROM contests
            WHERE id = ?
            """,
            (cid,),
        ).fetchone()
        if not row:
            raise ValueError("Конкурс не найден")
        if int(row["enabled"] or 0) != 1:
            raise ValueError("Конкурс выключен")
        if str(row["status"] or "active") != "active":
            raise ValueError("Конкурс завершен")
        if int(row["ends_ts"] or 0) <= now:
            raise ValueError("Конкурс уже закончился")

        conn.execute(
            """
            INSERT INTO contest_tickets (contest_id, tg_user_id, tg_username, tg_first_name, tg_last_name, qty, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cid,
                uid,
                str(tg_username).strip() if tg_username else None,
                str(tg_first_name).strip() if tg_first_name else None,
                str(tg_last_name).strip() if tg_last_name else None,
                q,
                now,
            ),
        )

        total_qty_row = conn.execute(
            "SELECT COALESCE(SUM(qty), 0) AS c FROM contest_tickets WHERE contest_id = ?",
            (cid,),
        ).fetchone()
        my_qty_row = conn.execute(
            "SELECT COALESCE(SUM(qty), 0) AS c FROM contest_tickets WHERE contest_id = ? AND tg_user_id = ?",
            (cid, uid),
        ).fetchone()
        participants_row = conn.execute(
            "SELECT COUNT(DISTINCT tg_user_id) AS c FROM contest_tickets WHERE contest_id = ?",
            (cid,),
        ).fetchone()

        conn.commit()
        return {
            "contest_id": cid,
            "tickets_total": int(total_qty_row["c"] if total_qty_row else 0),
            "my_tickets": int(my_qty_row["c"] if my_qty_row else 0),
            "participants": int(participants_row["c"] if participants_row else 0),
        }
    finally:
        conn.close()


def contests_finalize_due(*, now_ts: Optional[int] = None) -> List[Dict[str, Any]]:
    init_wins_db()
    now = int(now_ts) if now_ts is not None else int(time.time())
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, title, prize_gift_id
            FROM contests
            WHERE enabled = 1 AND COALESCE(status, 'active') = 'active' AND ends_ts <= ?
            ORDER BY ends_ts ASC
            """,
            (now,),
        ).fetchall()

        out: List[Dict[str, Any]] = []
        for r in rows:
            contest_id = str(r["id"])
            title = str(r["title"] or "Конкурс")
            prize_gift_id = str(r["prize_gift_id"]).strip() if r["prize_gift_id"] else None

            agg = conn.execute(
                """
                SELECT tg_user_id,
                       MAX(COALESCE(tg_username, '')) AS username,
                       MAX(COALESCE(tg_first_name, '')) AS first_name,
                       MAX(COALESCE(tg_last_name, '')) AS last_name,
                       SUM(qty) AS qty
                FROM contest_tickets
                WHERE contest_id = ?
                GROUP BY tg_user_id
                """,
                (contest_id,),
            ).fetchall()

            total = 0
            for a in agg:
                total += int(a["qty"] or 0)

            winner_id: Optional[int] = None
            winner_name: Optional[str] = None

            if total > 0:
                pick = random.randint(1, total)
                cur = 0
                winner_row = None
                for a in agg:
                    cur += int(a["qty"] or 0)
                    if cur >= pick:
                        winner_row = a
                        break
                if winner_row is not None:
                    winner_id = int(winner_row["tg_user_id"] or 0) or None
                    uname = str(winner_row["username"] or "").strip()
                    fn = str(winner_row["first_name"] or "").strip()
                    ln = str(winner_row["last_name"] or "").strip()
                    full = f"{fn} {ln}".strip()
                    winner_name = full or (f"@{uname}" if uname else (str(winner_id) if winner_id else None))

            conn.execute(
                """
                UPDATE contests
                SET status = 'completed',
                    winner_tg_user_id = ?,
                    winner_name = ?,
                    completed_ts = ?,
                    updated_ts = ?
                WHERE id = ?
                """,
                (winner_id, winner_name, now, now, contest_id),
            )

            out.append(
                {
                    "contest_id": contest_id,
                    "title": title,
                    "prize_gift_id": prize_gift_id,
                    "winner_tg_user_id": winner_id,
                    "winner_name": winner_name,
                }
            )

        conn.commit()
        return out
    finally:
        conn.close()


def create_payment_intent(
    *,
    provider: str,
    client_id: str,
    amount_ton: float,
    stars_amount: Optional[int],
    currency: Optional[str],
    provider_invoice_id: Optional[str],
    pay_url: Optional[str],
    idempotency_key: str,
    meta_json: Optional[str],
) -> str:
    init_wins_db()
    intent_id = secrets.token_urlsafe(16)
    prov = str(provider or "").strip().lower() or "unknown"
    cid = _normalize_client_id(client_id)
    idem = str(idempotency_key or "").strip()
    if not idem:
        raise ValueError("idempotency_key is required")

    now = int(time.time())
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO payment_intents (
              intent_id, provider, client_id, amount_ton, stars_amount, currency, status,
              provider_invoice_id, pay_url, telegram_payment_charge_id, provider_payment_charge_id,
              idempotency_key, created_ts, updated_ts, paid_ts, meta_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intent_id,
                prov,
                cid,
                float(amount_ton),
                int(stars_amount) if stars_amount is not None else None,
                str(currency or "").strip() or None,
                "created",
                str(provider_invoice_id).strip() if provider_invoice_id else None,
                str(pay_url).strip() if pay_url else None,
                None,
                None,
                idem,
                now,
                now,
                None,
                meta_json,
            ),
        )
        conn.commit()
        return intent_id
    finally:
        conn.close()


def get_payment_intent(*, intent_id: str) -> Optional[Dict[str, Any]]:
    init_wins_db()
    iid = str(intent_id or "").strip()
    if not iid:
        return None
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT intent_id, provider, client_id, amount_ton, stars_amount, currency, status,
                   provider_invoice_id, pay_url, telegram_payment_charge_id, provider_payment_charge_id,
                   idempotency_key, created_ts, updated_ts, paid_ts, meta_json
            FROM payment_intents
            WHERE intent_id = ?
            """,
            (iid,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_payment_intent(
    *,
    intent_id: str,
    status: Optional[str] = None,
    provider_invoice_id: Optional[str] = None,
    pay_url: Optional[str] = None,
    telegram_payment_charge_id: Optional[str] = None,
    provider_payment_charge_id: Optional[str] = None,
    paid_ts: Optional[int] = None,
    meta_json: Optional[str] = None,
) -> None:
    init_wins_db()
    iid = str(intent_id or "").strip()
    if not iid:
        raise ValueError("intent_id is required")
    now = int(time.time())
    fields = []
    args: List[Any] = []
    if status is not None:
        fields.append("status = ?")
        args.append(str(status or "").strip().lower() or "unknown")
    if provider_invoice_id is not None:
        fields.append("provider_invoice_id = ?")
        args.append(str(provider_invoice_id).strip() if provider_invoice_id else None)
    if pay_url is not None:
        fields.append("pay_url = ?")
        args.append(str(pay_url).strip() if pay_url else None)
    if telegram_payment_charge_id is not None:
        fields.append("telegram_payment_charge_id = ?")
        args.append(str(telegram_payment_charge_id).strip() if telegram_payment_charge_id else None)
    if provider_payment_charge_id is not None:
        fields.append("provider_payment_charge_id = ?")
        args.append(str(provider_payment_charge_id).strip() if provider_payment_charge_id else None)
    if paid_ts is not None:
        fields.append("paid_ts = ?")
        args.append(int(paid_ts) if paid_ts else None)
    if meta_json is not None:
        fields.append("meta_json = ?")
        args.append(meta_json)
    fields.append("updated_ts = ?")
    args.append(now)
    args.append(iid)
    conn = get_conn()
    try:
        conn.execute(
            f"UPDATE payment_intents SET {', '.join(fields)} WHERE intent_id = ?",
            tuple(args),
        )
        conn.commit()
    finally:
        conn.close()


def mark_payment_intent_paid(*, intent_id: str, paid_meta_json: Optional[str]) -> None:
    update_payment_intent(intent_id=intent_id, status="paid", paid_ts=int(time.time()), meta_json=paid_meta_json)


def get_payment_intent_by_tg_charge(*, telegram_payment_charge_id: str) -> Optional[Dict[str, Any]]:
    init_wins_db()
    cid = str(telegram_payment_charge_id or "").strip()
    if not cid:
        return None
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT intent_id, provider, client_id, amount_ton, stars_amount, currency, status,
                   provider_invoice_id, pay_url, telegram_payment_charge_id, provider_payment_charge_id,
                   idempotency_key, created_ts, updated_ts, paid_ts, meta_json
            FROM payment_intents
            WHERE telegram_payment_charge_id = ?
            LIMIT 1
            """,
            (cid,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return int(default)


def ton_to_coins(value_ton: float) -> int:
    try:
        v = float(value_ton)
    except Exception:
        raise ValueError("amount_ton is invalid")
    if not (v == v):
        raise ValueError("amount_ton is invalid")
    scale = int(COIN_SCALE_TON)
    if scale <= 0:
        scale = 1000
    return int(round(v * scale))


def coins_to_ton(value_coins: int) -> float:
    scale = int(COIN_SCALE_TON)
    if scale <= 0:
        scale = 1000
    try:
        return float(value_coins) / float(scale)
    except Exception:
        return 0.0


def _apply_coin_tx_conn(
    conn: sqlite3.Connection,
    *,
    client_id: str,
    amount_coins: int,
    tx_type: str,
    idempotency_key: str,
    meta_json: Optional[str] = None,
) -> int:
    tx_type_norm = str(tx_type or "").strip().lower() or "unknown"
    idem = str(idempotency_key or "").strip()
    if not idem:
        raise ValueError("idempotency_key is required")

    amount = int(amount_coins)
    if amount == 0:
        raise ValueError("amount_coins must be non-zero")

    existing = conn.execute(
        "SELECT 1 FROM coin_transactions WHERE idempotency_key = ?",
        (idem,),
    ).fetchone()
    if existing:
        row = conn.execute(
            "SELECT balance_coins FROM coin_wallet_clients WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        if not row:
            return 0
        return int(row["balance_coins"] or 0)

    row = conn.execute(
        "SELECT balance_coins FROM coin_wallet_clients WHERE client_id = ?",
        (client_id,),
    ).fetchone()
    current = int(row["balance_coins"]) if row else 0
    new_balance = current + int(amount)
    if new_balance < 0:
        raise ValueError("Недостаточно средств")

    now = int(time.time())
    conn.execute(
        """
        INSERT INTO coin_wallet_clients (client_id, balance_coins, updated_ts)
        VALUES (?, ?, ?)
        ON CONFLICT(client_id) DO UPDATE SET
          balance_coins = excluded.balance_coins,
          updated_ts = excluded.updated_ts
        """,
        (client_id, int(new_balance), now),
    )

    conn.execute(
        """
        INSERT INTO coin_transactions (ts, client_id, amount_coins, tx_type, idempotency_key, meta_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (now, client_id, int(amount), tx_type_norm, idem, meta_json),
    )

    return int(new_balance)


def _get_case_recent_stats(
    conn: sqlite3.Connection,
    *,
    client_id: str,
    window_sec: int,
) -> Dict[str, Any]:
    now = int(time.time())
    since = int(now - int(max(60, window_sec)))
    rows = conn.execute(
        """
        SELECT tx_type, SUM(amount_coins) AS s, COUNT(1) AS c
        FROM coin_transactions
        WHERE client_id = ? AND ts >= ? AND (tx_type = 'case_buy' OR tx_type = 'case_win')
        GROUP BY tx_type
        """,
        (client_id, since),
    ).fetchall()
    sums: Dict[str, Tuple[int, int]] = {}
    for r in rows:
        t = str(r["tx_type"] or "").strip().lower()
        try:
            s = int(r["s"] or 0)
        except Exception:
            s = 0
        try:
            c = int(r["c"] or 0)
        except Exception:
            c = 0
        sums[t] = (s, c)

    buy_sum = int(sums.get("case_buy", (0, 0))[0])
    buy_cnt = int(sums.get("case_buy", (0, 0))[1])
    win_sum = int(sums.get("case_win", (0, 0))[0])
    win_cnt = int(sums.get("case_win", (0, 0))[1])

    net_profit = int(win_sum + buy_sum)
    net_spent = int(-buy_sum) if buy_sum < 0 else 0

    return {
        "buy_count": int(buy_cnt),
        "win_count": int(win_cnt),
        "net_profit_coins": int(net_profit),
        "net_spent_coins": int(net_spent),
    }


def _normalize_client_id(client_id: str) -> str:
    client_norm = str(client_id or "").strip()
    if not client_norm:
        raise ValueError("client_id is required")
    if len(client_norm) < 3 or len(client_norm) > 128:
        raise ValueError("client_id is invalid")
    return client_norm


def _ton_to_cents(value_ton: float) -> int:
    try:
        v = float(value_ton)
    except Exception:
        raise ValueError("amount_ton is invalid")
    if not (v == v):
        raise ValueError("amount_ton is invalid")
    return int(round(v * 100.0))


def _cents_to_ton(value_cents: int) -> float:
    try:
        return float(int(value_cents)) / 100.0
    except Exception:
        return 0.0


def get_balance_client(*, client_id: str) -> float:
    init_wins_db()
    cid = _normalize_client_id(client_id)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT ton_balance_cents FROM wallet_clients WHERE client_id = ?",
            (cid,),
        ).fetchone()
        if not row:
            return 0.0
        return _cents_to_ton(row["ton_balance_cents"])
    finally:
        conn.close()


def get_coin_balance_client(*, client_id: str) -> int:
    init_wins_db()
    cid = _normalize_client_id(client_id)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT balance_coins FROM coin_wallet_clients WHERE client_id = ?",
            (cid,),
        ).fetchone()
        if not row:
            return 0
        return int(row["balance_coins"] or 0)
    finally:
        conn.close()


def apply_coin_tx(
    *,
    client_id: str,
    amount_coins: int,
    tx_type: str,
    idempotency_key: str,
    meta_json: Optional[str] = None,
) -> int:
    init_wins_db()
    cid = _normalize_client_id(client_id)
    tx_type_norm = str(tx_type or "").strip().lower() or "unknown"
    idem = str(idempotency_key or "").strip()
    if not idem:
        raise ValueError("idempotency_key is required")

    amount = int(amount_coins)
    if amount == 0:
        raise ValueError("amount_coins must be non-zero")

    conn = get_conn()
    try:
        now = int(time.time())
        conn.execute("BEGIN IMMEDIATE")

        existing = conn.execute(
            "SELECT 1 FROM coin_transactions WHERE idempotency_key = ?",
            (idem,),
        ).fetchone()
        if existing:
            row = conn.execute(
                "SELECT balance_coins FROM coin_wallet_clients WHERE client_id = ?",
                (cid,),
            ).fetchone()
            if not row:
                return 0
            return int(row["balance_coins"] or 0)

        row = conn.execute(
            "SELECT balance_coins FROM coin_wallet_clients WHERE client_id = ?",
            (cid,),
        ).fetchone()
        current = int(row["balance_coins"]) if row else 0
        new_balance = current + int(amount)
        if new_balance < 0:
            raise ValueError("Недостаточно средств")

        conn.execute(
            """
            INSERT INTO coin_wallet_clients (client_id, balance_coins, updated_ts)
            VALUES (?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
              balance_coins = excluded.balance_coins,
              updated_ts = excluded.updated_ts
            """,
            (cid, int(new_balance), now),
        )

        conn.execute(
            """
            INSERT INTO coin_transactions (ts, client_id, amount_coins, tx_type, idempotency_key, meta_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (now, cid, int(amount), tx_type_norm, idem, meta_json),
        )

        conn.commit()
        return int(new_balance)
    finally:
        conn.close()


def _get_player_state(conn: sqlite3.Connection, *, client_id: str) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT client_id, loss_streak FROM player_state WHERE client_id = ?",
        (client_id,),
    ).fetchone()
    if not row:
        return {"client_id": client_id, "loss_streak": 0}
    return {"client_id": client_id, "loss_streak": int(row["loss_streak"] or 0)}


def _set_loss_streak(conn: sqlite3.Connection, *, client_id: str, loss_streak: int, now: int) -> None:
    conn.execute(
        """
        INSERT INTO player_state (client_id, loss_streak, updated_ts)
        VALUES (?, ?, ?)
        ON CONFLICT(client_id) DO UPDATE SET
          loss_streak = excluded.loss_streak,
          updated_ts = excluded.updated_ts
        """,
        (client_id, int(max(0, loss_streak)), int(now)),
    )


def ensure_default_cases() -> None:
    init_wins_db()
    conn = get_conn()
    try:
        row = conn.execute("SELECT 1 FROM cases LIMIT 1").fetchone()
        have_cases = bool(row)

        defaults = [
            ("free", "Для новичков", 0, "winter", 1, 10),
            ("snow", "Снежный", 2500, "winter", 1, 20),
            ("ginger", "Пряник", 5000, "winter", 1, 30),
            ("ice", "Лёд", 10000, "winter", 1, 40),
            ("spark", "Салют", 20000, "winter", 1, 50),
            ("santa", "Санта", 50000, "winter", 1, 60),
            ("premium_black", "Black Premium", 120000, "black", 1, 70),
        ]
        if not have_cases:
            conn.executemany(
                "INSERT INTO cases (id, name, price_coins, theme, enabled, sort_order) VALUES (?, ?, ?, ?, ?, ?)",
                defaults,
            )

        # payouts are in COIN. 1000 COIN = 1 TON
        # low: 0.01/0.1 TON; mid: 1 TON; high: 5 TON
        drops = [
            ("free", "coins", None, 10, 1.0, "low"),
            ("free", "coins", None, 100, 0.55, "low"),
            ("free", "coins", None, 1000, 0.16, "mid"),
            ("free", "coins", None, 5000, 0.04, "high"),
            ("free", "gift", "winter-wreath-moon-cat", 0, 0.006, "high"),

            ("snow", "coins", None, 10, 1.0, "low"),
            ("snow", "coins", None, 100, 0.70, "low"),
            ("snow", "coins", None, 1000, 0.22, "mid"),
            ("snow", "coins", None, 5000, 0.06, "high"),
            ("snow", "gift", "lol-pop-shock-wave", 0, 0.0030, "high"),

            ("ginger", "coins", None, 10, 1.0, "low"),
            ("ginger", "coins", None, 100, 0.65, "low"),
            ("ginger", "coins", None, 1000, 0.25, "mid"),
            ("ginger", "coins", None, 5000, 0.07, "high"),
            ("ginger", "gift", "voodoo-doll-terminator", 0, 0.0025, "high"),

            ("ice", "coins", None, 10, 1.0, "low"),
            ("ice", "coins", None, 100, 0.58, "low"),
            ("ice", "coins", None, 1000, 0.30, "mid"),
            ("ice", "coins", None, 5000, 0.10, "high"),
            ("ice", "gift", "durov-cap", 0, 0.0022, "high"),

            ("spark", "coins", None, 10, 1.0, "low"),
            ("spark", "coins", None, 100, 0.50, "low"),
            ("spark", "coins", None, 1000, 0.34, "mid"),
            ("spark", "coins", None, 5000, 0.12, "high"),
            ("spark", "gift", "input-key-ninja", 0, 0.0018, "high"),

            ("santa", "coins", None, 10, 1.0, "low"),
            ("santa", "coins", None, 100, 0.40, "low"),
            ("santa", "coins", None, 1000, 0.40, "mid"),
            ("santa", "coins", None, 5000, 0.18, "high"),
            ("santa", "gift", "durovs-cap-captain", 0, 0.0014, "high"),

            ("premium_black", "coins", None, 10, 1.0, "low"),
            ("premium_black", "coins", None, 100, 0.34, "low"),
            ("premium_black", "coins", None, 1000, 0.46, "mid"),
            ("premium_black", "coins", None, 5000, 0.28, "high"),
            ("premium_black", "gift", "heart-locket-angelic-pink", 0, 0.0012, "high"),
        ]

        # If DB already had legacy drops, migrate to the new scale.
        if have_cases:
            legacy = conn.execute(
                "SELECT 1 FROM case_drops WHERE payout_coins >= 10000 LIMIT 1"
            ).fetchone()
            missing_gifts = conn.execute(
                "SELECT 1 FROM case_drops WHERE reward_type = 'gift' LIMIT 1"
            ).fetchone() is None
            if legacy or missing_gifts:
                conn.execute("DELETE FROM case_drops")

        conn.executemany(
            """
            INSERT INTO case_drops (case_id, reward_type, reward_id, payout_coins, weight, tier)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            drops,
        )

        conn.commit()
    finally:
        conn.close()


def list_cases(*, include_disabled: bool = False) -> List[Dict[str, Any]]:
    ensure_default_cases()
    conn = get_conn()
    try:
        if include_disabled:
            rows = conn.execute(
                "SELECT id, name, price_coins, theme, enabled, sort_order FROM cases ORDER BY sort_order ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, name, price_coins, theme, enabled, sort_order
                FROM cases
                WHERE enabled = 1
                ORDER BY sort_order ASC
                """
            ).fetchall()

        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "price_coins": int(r["price_coins"] or 0),
                    "price_ton": coins_to_ton(int(r["price_coins"] or 0)),
                    "theme": r["theme"],
                    "enabled": bool(int(r["enabled"] or 0)),
                    "sort_order": int(r["sort_order"] or 0),
                }
            )
        return out
    finally:
        conn.close()


def _pick_weighted(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not items:
        return None
    total = 0.0
    for it in items:
        try:
            w = float(it.get("weight") or 0)
        except Exception:
            w = 0.0
        if w > 0:
            total += w
    if total <= 0:
        return items[0]

    import random

    r = random.random() * total
    for it in items:
        w = float(it.get("weight") or 0)
        if w <= 0:
            continue
        r -= w
        if r <= 0:
            return it
    return items[0]


def set_app_setting(*, key: str, value: str) -> None:
    init_wins_db()
    key_norm = str(key or "").strip().lower()
    if not key_norm:
        raise ValueError("key is required")
    val = str(value if value is not None else "")
    now = int(time.time())
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_ts)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              value = excluded.value,
              updated_ts = excluded.updated_ts
            """,
            (key_norm, val, now),
        )
        conn.commit()
    finally:
        conn.close()


def get_app_setting(*, key: str, default: Optional[str] = None) -> Optional[str]:
    init_wins_db()
    key_norm = str(key or "").strip().lower()
    if not key_norm:
        raise ValueError("key is required")
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key_norm,)).fetchone()
        if not row:
            return default
        return str(row["value"])
    finally:
        conn.close()


def _get_setting_int_conn(conn: sqlite3.Connection, *, key: str, default: int) -> int:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (str(key).strip().lower(),)).fetchone()
    if not row:
        return int(default)
    try:
        return int(float(str(row["value"]).strip()))
    except Exception:
        return int(default)


def _get_setting_float_conn(conn: sqlite3.Connection, *, key: str, default: float) -> float:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (str(key).strip().lower(),)).fetchone()
    if not row:
        return float(default)
    try:
        return float(str(row["value"]).strip().replace(",", "."))
    except Exception:
        return float(default)


def admin_list_cases() -> List[Dict[str, Any]]:
    ensure_default_cases()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name, price_coins, theme, enabled, sort_order FROM cases ORDER BY sort_order ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_topups(*, client_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    init_wins_db()
    cid = _normalize_client_id(client_id)
    lim = int(max(1, min(100, int(limit))))
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT
              intent_id,
              provider,
              client_id,
              amount_ton,
              stars_amount,
              currency,
              status,
              provider_invoice_id,
              telegram_payment_charge_id,
              provider_payment_charge_id,
              created_ts,
              updated_ts,
              paid_ts
            FROM payment_intents
            WHERE client_id = ?
              AND status = 'paid'
            ORDER BY COALESCE(paid_ts, updated_ts, created_ts) DESC
            LIMIT ?
            """,
            (cid, lim),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def admin_update_case(
    *,
    case_id: str,
    name: Optional[str] = None,
    price_coins: Optional[int] = None,
    enabled: Optional[bool] = None,
    sort_order: Optional[int] = None,
    theme: Optional[str] = None,
) -> None:
    ensure_default_cases()
    cid = str(case_id or "").strip()
    if not cid:
        raise ValueError("case_id is required")
    conn = get_conn()
    try:
        row = conn.execute("SELECT id FROM cases WHERE id = ?", (cid,)).fetchone()
        if not row:
            raise ValueError("Кейс не найден")
        fields: List[str] = []
        vals: List[Any] = []
        if name is not None:
            fields.append("name = ?")
            vals.append(str(name))
        if price_coins is not None:
            fields.append("price_coins = ?")
            vals.append(int(price_coins))
        if enabled is not None:
            fields.append("enabled = ?")
            vals.append(1 if enabled else 0)
        if sort_order is not None:
            fields.append("sort_order = ?")
            vals.append(int(sort_order))
        if theme is not None:
            fields.append("theme = ?")
            vals.append(str(theme))
        if not fields:
            return
        vals.append(cid)
        conn.execute(f"UPDATE cases SET {', '.join(fields)} WHERE id = ?", tuple(vals))
        conn.commit()
    finally:
        conn.close()


def admin_list_case_drops(*, case_id: str) -> List[Dict[str, Any]]:
    ensure_default_cases()
    cid = str(case_id or "").strip()
    if not cid:
        raise ValueError("case_id is required")
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, case_id, reward_type, reward_id, payout_coins, weight, tier
            FROM case_drops
            WHERE case_id = ?
            ORDER BY id ASC
            """,
            (cid,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def admin_add_case_drop(
    *,
    case_id: str,
    reward_type: str,
    reward_id: Optional[str],
    payout_coins: int,
    weight: float,
    tier: Optional[str],
) -> int:
    ensure_default_cases()
    cid = str(case_id or "").strip()
    if not cid:
        raise ValueError("case_id is required")
    rt = str(reward_type or "").strip().lower()
    if rt not in ("coins", "gift"):
        raise ValueError("reward_type must be coins|gift")
    rid = None
    if reward_id is not None:
        rid = str(reward_id).strip() or None
    tier_norm = str(tier or "").strip().lower() or None
    if tier_norm is not None and tier_norm not in ("low", "mid", "high"):
        raise ValueError("tier must be low|mid|high")
    w = float(weight)
    if not (w > 0):
        raise ValueError("weight must be > 0")
    payout = int(payout_coins)
    if payout < 0:
        raise ValueError("payout_coins must be >= 0")

    conn = get_conn()
    try:
        row = conn.execute("SELECT 1 FROM cases WHERE id = ?", (cid,)).fetchone()
        if not row:
            raise ValueError("Кейс не найден")
        cur = conn.execute(
            """
            INSERT INTO case_drops (case_id, reward_type, reward_id, payout_coins, weight, tier)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (cid, rt, rid, payout, float(w), tier_norm),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def admin_delete_case_drop(*, drop_id: int) -> None:
    ensure_default_cases()
    did = int(drop_id)
    conn = get_conn()
    try:
        conn.execute("DELETE FROM case_drops WHERE id = ?", (did,))
        conn.commit()
    finally:
        conn.close()


def admin_list_top_coin_balances(*, limit: int = 20) -> List[Dict[str, Any]]:
    init_wins_db()
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT client_id, balance_coins, updated_ts
            FROM coin_wallet_clients
            ORDER BY balance_coins DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def admin_list_coin_transactions(*, limit: int = 50, client_id: Optional[str] = None) -> List[Dict[str, Any]]:
    init_wins_db()
    conn = get_conn()
    try:
        if client_id:
            cid = _normalize_client_id(client_id)
            rows = conn.execute(
                """
                SELECT id, ts, client_id, amount_coins, tx_type, idempotency_key, meta_json
                FROM coin_transactions
                WHERE client_id = ?
                ORDER BY ts DESC, id DESC
                LIMIT ?
                """,
                (cid, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, ts, client_id, amount_coins, tx_type, idempotency_key, meta_json
                FROM coin_transactions
                ORDER BY ts DESC, id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def open_case(
    *,
    client_id: str,
    case_id: str,
    demo: bool,
    idempotency_key: str,
    mult: int = 1,
    meta_json: Optional[str] = None,
) -> Dict[str, Any]:
    ensure_default_cases()
    cid = _normalize_client_id(client_id)
    case_norm = str(case_id or "").strip()
    if not case_norm:
        raise ValueError("case_id is required")
    idem = str(idempotency_key or "").strip()
    if not idem:
        raise ValueError("idempotency_key is required")

    conn = get_conn()
    try:
        now = int(time.time())
        conn.execute("BEGIN IMMEDIATE")

        mid_threshold = _get_setting_int_conn(conn, key="case.loss_mid_threshold", default=2)
        high_threshold = _get_setting_int_conn(conn, key="case.loss_high_threshold", default=4)
        demo_mult = _get_setting_float_conn(conn, key="case.demo_weight_mult", default=1.4)

        case_row = conn.execute(
            "SELECT id, name, price_coins, enabled FROM cases WHERE id = ?",
            (case_norm,),
        ).fetchone()
        if not case_row:
            raise ValueError("Кейс не найден")
        if int(case_row["enabled"] or 0) != 1:
            raise ValueError("Кейс выключен")

        price = int(case_row["price_coins"] or 0)
        total_price = int(price) * int(mult_norm)
        if not demo and total_price > 0:
            row_bal = conn.execute(
                "SELECT balance_coins FROM coin_wallet_clients WHERE client_id = ?",
                (cid,),
            ).fetchone()
            current_bal = int(row_bal["balance_coins"]) if row_bal else 0
            if current_bal < total_price:
                raise ValueError("Недостаточно средств")

        drops_rows = conn.execute(
            """
            SELECT reward_type, reward_id, payout_coins, weight, tier
            FROM case_drops
            WHERE case_id = ?
            """,
            (case_norm,),
        ).fetchall()

        drops: List[Dict[str, Any]] = []
        for r in drops_rows:
            drops.append(
                {
                    "reward_type": str(r["reward_type"] or "").strip().lower() or "coins",
                    "reward_id": r["reward_id"],
                    "payout_coins": _safe_int(r["payout_coins"], 0),
                    "weight": float(r["weight"] or 0.0),
                    "tier": str(r["tier"] or "").strip().lower() or None,
                }
            )

        if not drops:
            raise ValueError("Кейс пуст")

        recent_window_sec = int(cfg("cases.anti_abuse.recent_window_sec", 86400) or 86400)
        heavy_threshold = int(cfg("cases.anti_abuse.heavy_opener_threshold", 30) or 30)
        profit_ratio_threshold = float(cfg("cases.anti_abuse.profit_threshold_ratio", 0.15) or 0.15)
        ev_target_ratio = float(cfg("cases.ev_target_ratio", 0.72) or 0.72)

        stats = _get_case_recent_stats(conn, client_id=cid, window_sec=recent_window_sec)
        buy_count = int(stats.get("buy_count") or 0)
        net_profit_coins = int(stats.get("net_profit_coins") or 0)
        net_spent_coins = int(stats.get("net_spent_coins") or 0)

        state = _get_player_state(conn, client_id=cid)
        loss_streak = int(state.get("loss_streak") or 0)

        target_tier = None
        if loss_streak >= int(high_threshold):
            target_tier = "high"
        elif loss_streak >= int(mid_threshold):
            target_tier = "mid"

        if demo:
            if target_tier == "mid":
                target_tier = "high"
            elif target_tier is None:
                target_tier = "mid"

        eligible = [dict(d) for d in drops]
        if target_tier:
            by_tier = [d for d in drops if d.get("tier") == target_tier]
            if by_tier:
                eligible = [dict(d) for d in by_tier]

        if demo:
            for d in eligible:
                if d.get("tier") in ("mid", "high"):
                    d["weight"] = float(d.get("weight") or 0) * float(demo_mult)

        if buy_count <= 0:
            if secrets.randbelow(10000) / 10000.0 < float(cfg("cases.weight_adjustment.new_player_boost_chance", 0.07) or 0.07):
                for d in eligible:
                    t = str(d.get("tier") or "").strip().lower()
                    if t == "mid":
                        d["weight"] = float(d.get("weight") or 0) * float(cfg("cases.weight_adjustment.new_player_mid_mult", 1.15) or 1.15)
                    elif t == "high":
                        d["weight"] = float(d.get("weight") or 0) * float(cfg("cases.weight_adjustment.new_player_high_mult", 1.06) or 1.06)

        if buy_count >= heavy_threshold:
            for d in eligible:
                t = str(d.get("tier") or "").strip().lower()
                if t == "high":
                    d["weight"] = float(d.get("weight") or 0) * float(cfg("cases.weight_adjustment.heavy_opener_high_mult", 0.72) or 0.72)
                elif t == "mid":
                    d["weight"] = float(d.get("weight") or 0) * float(cfg("cases.weight_adjustment.heavy_opener_mid_mult", 0.90) or 0.90)
                elif t == "low":
                    d["weight"] = float(d.get("weight") or 0) * float(cfg("cases.weight_adjustment.heavy_opener_low_mult", 1.08) or 1.08)

        if net_spent_coins > 0 and (float(net_profit_coins) / float(net_spent_coins)) >= float(profit_ratio_threshold):
            for d in eligible:
                t = str(d.get("tier") or "").strip().lower()
                if t == "high":
                    d["weight"] = float(d.get("weight") or 0) * float(cfg("cases.weight_adjustment.recent_profit_high_mult", 0.78) or 0.78)
                elif t == "mid":
                    d["weight"] = float(d.get("weight") or 0) * float(cfg("cases.weight_adjustment.recent_profit_mid_mult", 0.92) or 0.92)
                elif t == "low":
                    d["weight"] = float(d.get("weight") or 0) * float(cfg("cases.weight_adjustment.recent_profit_low_mult", 1.06) or 1.06)

        def _ev(items: List[Dict[str, Any]]) -> float:
            total_w = 0.0
            total_v = 0.0
            for it in items:
                try:
                    w = float(it.get("weight") or 0)
                except Exception:
                    w = 0.0
                if w <= 0:
                    continue
                total_w += w
                total_v += w * float(int(it.get("payout_coins") or 0))
            if total_w <= 0:
                return 0.0
            return total_v / total_w

        if price > 0 and ev_target_ratio > 0:
            target_ev = float(price) * float(ev_target_ratio)
            for _ in range(3):
                cur_ev = _ev(eligible)
                if cur_ev <= target_ev:
                    break
                ratio = max(0.10, min(1.0, target_ev / max(1.0, cur_ev)))
                for d in eligible:
                    t = str(d.get("tier") or "").strip().lower()
                    if t == "high":
                        d["weight"] = float(d.get("weight") or 0) * ratio
                cur_ev2 = _ev(eligible)
                if cur_ev2 <= target_ev:
                    break
                ratio2 = max(0.15, min(1.0, target_ev / max(1.0, cur_ev2)))
                for d in eligible:
                    t = str(d.get("tier") or "").strip().lower()
                    if t == "mid":
                        d["weight"] = float(d.get("weight") or 0) * ratio2

        if not demo and total_price > 0:
            _apply_coin_tx_conn(
                conn,
                client_id=cid,
                amount_coins=-int(total_price),
                tx_type="case_buy",
                idempotency_key=f"{idem}:buy",
                meta_json=meta_json,
            )

        items_out: List[Dict[str, Any]] = []
        total_payout = 0
        any_win = False
        for i in range(int(mult_norm)):
            picked = _pick_weighted(eligible)
            if not picked:
                raise ValueError("Не получилось выбрать дроп")
            payout = int(picked.get("payout_coins") or 0)
            total_payout += int(payout)
            picked_tier = str(picked.get("tier") or "").strip().lower() or None
            if payout > 0:
                any_win = True
            items_out.append(
                {
                    "reward_type": str(picked.get("reward_type") or "coins"),
                    "reward_id": picked.get("reward_id"),
                    "tier": picked_tier,
                    "payout_coins": int(payout),
                }
            )

            if not demo and payout > 0:
                _apply_coin_tx_conn(
                    conn,
                    client_id=cid,
                    amount_coins=int(payout),
                    tx_type="case_win",
                    idempotency_key=f"{idem}:win:{i}",
                    meta_json=meta_json,
                )

        row_after = conn.execute(
            "SELECT balance_coins FROM coin_wallet_clients WHERE client_id = ?",
            (cid,),
        ).fetchone()
        new_balance = int(row_after["balance_coins"] or 0) if row_after else 0

        is_loss = True
        if price <= 0:
            is_loss = not any_win
        else:
            is_loss = int(total_payout) < int(round(float(total_price) * 0.70))

        new_streak = (loss_streak + 1) if is_loss else 0
        _set_loss_streak(conn, client_id=cid, loss_streak=new_streak, now=now)

        conn.commit()
        if mult_norm <= 1:
            one = items_out[0] if items_out else {"reward_type": "coins", "reward_id": None, "tier": None, "payout_coins": 0}
            return {
                "case_id": case_norm,
                "case_name": case_row["name"],
                "price_coins": int(price),
                "reward_type": one.get("reward_type"),
                "reward_id": one.get("reward_id"),
                "tier": one.get("tier"),
                "payout_coins": int(one.get("payout_coins") or 0),
                "balance_coins": int(new_balance),
                "balance_ton": coins_to_ton(int(new_balance)),
                "loss_streak": int(new_streak),
                "demo": bool(demo),
                "mult": int(mult_norm),
            }
        return {
            "case_id": case_norm,
            "case_name": case_row["name"],
            "price_coins": int(price),
            "total_price_coins": int(total_price),
            "total_payout_coins": int(total_payout),
            "items": items_out,
            "balance_coins": int(new_balance),
            "balance_ton": coins_to_ton(int(new_balance)),
            "loss_streak": int(new_streak),
            "demo": bool(demo),
            "mult": int(mult_norm),
        }
    finally:
        conn.close()


def apply_client_tx(
    *,
    client_id: str,
    amount_ton: float,
    tx_type: str,
    idempotency_key: str,
    meta_json: Optional[str] = None,
) -> float:
    init_wins_db()
    cid = _normalize_client_id(client_id)
    tx_type_norm = str(tx_type or "").strip().lower() or "unknown"
    idem = str(idempotency_key or "").strip()
    if not idem:
        raise ValueError("idempotency_key is required")

    amount_cents = _ton_to_cents(amount_ton)
    if amount_cents == 0:
        raise ValueError("amount_ton must be non-zero")

    conn = get_conn()
    try:
        now = int(time.time())
        conn.execute("BEGIN IMMEDIATE")

        existing = conn.execute(
            "SELECT 1 FROM client_transactions WHERE idempotency_key = ?",
            (idem,),
        ).fetchone()
        if existing:
            row = conn.execute(
                "SELECT ton_balance_cents FROM wallet_clients WHERE client_id = ?",
                (cid,),
            ).fetchone()
            if not row:
                return 0.0
            return _cents_to_ton(row["ton_balance_cents"])

        row = conn.execute(
            "SELECT ton_balance_cents FROM wallet_clients WHERE client_id = ?",
            (cid,),
        ).fetchone()
        current_cents = int(row["ton_balance_cents"]) if row else 0
        new_cents = current_cents + int(amount_cents)
        if new_cents < 0:
            raise ValueError("Недостаточно средств")

        conn.execute(
            """
            INSERT INTO wallet_clients (client_id, ton_balance_cents, updated_ts)
            VALUES (?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
              ton_balance_cents = excluded.ton_balance_cents,
              updated_ts = excluded.updated_ts
            """,
            (cid, int(new_cents), now),
        )

        conn.execute(
            """
            INSERT INTO client_transactions (ts, client_id, amount_cents, tx_type, idempotency_key, meta_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (now, cid, int(amount_cents), tx_type_norm, idem, meta_json),
        )

        if tx_type_norm.startswith("topup") and int(amount_cents) > 0:
            _apply_referral_deposit_reward_conn(
                conn,
                invited_client_id=cid,
                source_event_id=str(idem),
                deposit_amount_cents=int(amount_cents),
                now_ts=now,
            )

        _unlock_referral_rewards_conn(conn, now_ts=now)

        conn.commit()
        return _cents_to_ton(new_cents)
    finally:
        conn.close()


def _ref_percent() -> float:
    try:
        v = float(str(os.getenv("REFERRAL_PERCENT", "0.10")).strip().replace(",", "."))
    except Exception:
        v = 0.10
    if v < 0:
        v = 0.0
    if v > 1:
        v = 1.0
    return float(v)


def _ref_lock_days() -> int:
    try:
        d = int(float(str(os.getenv("REFERRAL_LOCK_DAYS", "21")).strip().replace(",", ".")))
    except Exception:
        d = 21
    return int(max(0, d))


def _make_ref_code() -> str:
    raw = secrets.token_urlsafe(8)
    code = raw.replace("-", "").replace("_", "").upper()
    return code[:10]


def touch_ref_client(*, client_id: str, ip: Optional[str] = None, user_agent: Optional[str] = None) -> None:
    init_wins_db()
    cid = _normalize_client_id(client_id)
    now = int(time.time())
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT created_ts FROM referral_clients WHERE client_id = ?", (cid,)).fetchone()
        created_ts = int(row["created_ts"]) if row else now
        conn.execute(
            """
            INSERT INTO referral_clients (client_id, created_ts, last_seen_ts, last_ip, last_user_agent)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
              last_seen_ts = excluded.last_seen_ts,
              last_ip = COALESCE(excluded.last_ip, referral_clients.last_ip),
              last_user_agent = COALESCE(excluded.last_user_agent, referral_clients.last_user_agent)
            """,
            (cid, created_ts, now, str(ip).strip() if ip else None, str(user_agent).strip() if user_agent else None),
        )
        conn.commit()
    finally:
        conn.close()


def get_referral_code(*, client_id: str) -> str:
    init_wins_db()
    cid = _normalize_client_id(client_id)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT code FROM referral_codes WHERE inviter_client_id = ? LIMIT 1",
            (cid,),
        ).fetchone()
        if row and row["code"]:
            return str(row["code"])

        now = int(time.time())
        for _ in range(10):
            code = _make_ref_code()
            try:
                conn.execute(
                    "INSERT INTO referral_codes (code, inviter_client_id, created_ts) VALUES (?, ?, ?)",
                    (code, cid, now),
                )
                conn.commit()
                return code
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("Failed to generate unique referral code")
    finally:
        conn.close()


def bind_referral(
    *,
    invited_client_id: str,
    inviter_code: str,
    invited_ip: Optional[str] = None,
    invited_user_agent: Optional[str] = None,
) -> bool:
    init_wins_db()
    invited_cid = _normalize_client_id(invited_client_id)
    code = str(inviter_code or "").strip().upper()
    if not code:
        raise ValueError("inviter_code is required")

    now = int(time.time())
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")

        code_row = conn.execute(
            "SELECT inviter_client_id FROM referral_codes WHERE code = ? LIMIT 1",
            (code,),
        ).fetchone()
        if not code_row:
            raise ValueError("invalid referral code")
        inviter_cid = str(code_row["inviter_client_id"])
        if inviter_cid == invited_cid:
            raise ValueError("self-referral")

        existing = conn.execute(
            "SELECT invited_client_id FROM referrals WHERE invited_client_id = ? LIMIT 1",
            (invited_cid,),
        ).fetchone()
        if existing:
            conn.commit()
            return False

        window_sec = int(float(os.getenv("REFERRAL_BIND_WINDOW_SECONDS", str(24 * 3600))))
        if window_sec > 0:
            rc = conn.execute(
                "SELECT created_ts FROM referral_clients WHERE client_id = ? LIMIT 1",
                (invited_cid,),
            ).fetchone()
            if rc and (now - int(rc["created_ts"] or now)) > window_sec:
                conn.commit()
                return False

        conn.execute(
            """
            INSERT INTO referrals (
              invited_client_id, inviter_client_id, code, created_ts, status, fraud_reason, invited_ip, invited_user_agent
            )
            VALUES (?, ?, ?, ?, 'pending', NULL, ?, ?)
            """,
            (
                invited_cid,
                inviter_cid,
                code,
                now,
                str(invited_ip).strip() if invited_ip else None,
                str(invited_user_agent).strip() if invited_user_agent else None,
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def _ensure_ref_wallet_conn(conn: sqlite3.Connection, *, client_id: str, now_ts: int) -> None:
    conn.execute(
        """
        INSERT INTO referral_wallet (client_id, available_cents, locked_cents, updated_ts)
        VALUES (?, 0, 0, ?)
        ON CONFLICT(client_id) DO NOTHING
        """,
        (client_id, int(now_ts)),
    )


def _apply_referral_deposit_reward_conn(
    conn: sqlite3.Connection,
    *,
    invited_client_id: str,
    source_event_id: str,
    deposit_amount_cents: int,
    now_ts: int,
) -> None:
    row = conn.execute(
        "SELECT inviter_client_id, status FROM referrals WHERE invited_client_id = ? LIMIT 1",
        (invited_client_id,),
    ).fetchone()
    if not row:
        return
    if str(row["status"] or "").lower() in ("fraud", "rejected"):
        return
    inviter_client_id = str(row["inviter_client_id"])
    percent = _ref_percent()
    if percent <= 0:
        return
    amt = int(round(int(deposit_amount_cents) * percent))
    if amt <= 0:
        return

    lock_days = _ref_lock_days()
    available_ts = int(now_ts + lock_days * 86400) if lock_days > 0 else int(now_ts)
    reward_type = "deposit_percent"

    existing = conn.execute(
        """
        SELECT 1 FROM referral_rewards
        WHERE beneficiary_client_id = ? AND reward_type = ? AND source_event_id = ?
        LIMIT 1
        """,
        (inviter_client_id, reward_type, source_event_id),
    ).fetchone()
    if existing:
        return

    _ensure_ref_wallet_conn(conn, client_id=inviter_client_id, now_ts=now_ts)

    conn.execute(
        """
        INSERT INTO referral_rewards (
          created_ts, beneficiary_client_id, invited_client_id,
          reward_type, amount_cents, status, available_ts,
          source_event_id, source_amount_cents,
          reversed_ts, reverse_reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
        """,
        (
            int(now_ts),
            inviter_client_id,
            invited_client_id,
            reward_type,
            int(amt),
            "locked" if lock_days > 0 else "available",
            int(available_ts) if lock_days > 0 else None,
            str(source_event_id),
            int(deposit_amount_cents),
        ),
    )

    if lock_days > 0:
        conn.execute(
            """
            UPDATE referral_wallet
            SET locked_cents = locked_cents + ?, updated_ts = ?
            WHERE client_id = ?
            """,
            (int(amt), int(now_ts), inviter_client_id),
        )
    else:
        conn.execute(
            """
            UPDATE referral_wallet
            SET available_cents = available_cents + ?, updated_ts = ?
            WHERE client_id = ?
            """,
            (int(amt), int(now_ts), inviter_client_id),
        )


def _unlock_referral_rewards_conn(conn: sqlite3.Connection, *, now_ts: int) -> int:
    rows = conn.execute(
        """
        SELECT id, beneficiary_client_id, amount_cents
        FROM referral_rewards
        WHERE status = 'locked' AND available_ts IS NOT NULL AND available_ts <= ?
        ORDER BY available_ts ASC
        LIMIT 500
        """,
        (int(now_ts),),
    ).fetchall()
    if not rows:
        return 0

    moved = 0
    for r in rows:
        rid = int(r["id"])
        cid = str(r["beneficiary_client_id"])
        amt = int(r["amount_cents"] or 0)
        if amt <= 0:
            conn.execute("UPDATE referral_rewards SET status = 'available' WHERE id = ?", (rid,))
            continue

        _ensure_ref_wallet_conn(conn, client_id=cid, now_ts=now_ts)
        conn.execute(
            "UPDATE referral_rewards SET status = 'available' WHERE id = ? AND status = 'locked'",
            (rid,),
        )
        conn.execute(
            """
            UPDATE referral_wallet
            SET locked_cents = locked_cents - ?,
                available_cents = available_cents + ?,
                updated_ts = ?
            WHERE client_id = ?
            """,
            (amt, amt, int(now_ts), cid),
        )
        moved += 1
    return moved


def get_referral_overview(*, client_id: str) -> Dict[str, Any]:
    init_wins_db()
    cid = _normalize_client_id(client_id)
    now = int(time.time())
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _unlock_referral_rewards_conn(conn, now_ts=now)
        _ensure_ref_wallet_conn(conn, client_id=cid, now_ts=now)

        w = conn.execute(
            "SELECT available_cents, locked_cents FROM referral_wallet WHERE client_id = ?",
            (cid,),
        ).fetchone()
        available_cents = int(w["available_cents"] or 0) if w else 0
        locked_cents = int(w["locked_cents"] or 0) if w else 0

        invited_total = conn.execute(
            "SELECT COUNT(1) AS c FROM referrals WHERE inviter_client_id = ?",
            (cid,),
        ).fetchone()
        invited_count = int(invited_total["c"] or 0) if invited_total else 0

        invited_active = conn.execute(
            "SELECT COUNT(1) AS c FROM referrals WHERE inviter_client_id = ? AND status = 'active'",
            (cid,),
        ).fetchone()
        active_count = int(invited_active["c"] or 0) if invited_active else 0

        earned = conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents), 0) AS s
            FROM referral_rewards
            WHERE beneficiary_client_id = ? AND status IN ('locked', 'available')
            """,
            (cid,),
        ).fetchone()
        earned_cents = int(earned["s"] or 0) if earned else 0

        conn.commit()
        return {
            "client_id": cid,
            "available_ton": _cents_to_ton(available_cents),
            "locked_ton": _cents_to_ton(locked_cents),
            "invited_count": invited_count,
            "active_count": active_count,
            "earned_ton": _cents_to_ton(earned_cents),
            "percent": _ref_percent(),
            "lock_days": _ref_lock_days(),
        }
    finally:
        conn.close()


def _make_promo_code() -> str:
    # short readable code
    raw = secrets.token_urlsafe(6)
    return raw.replace("-", "").replace("_", "").upper()[:10]


def _normalize_promo_code(code: str) -> str:
    code_norm = str(code or "").strip().upper()
    if not code_norm:
        raise ValueError("code is required")
    if len(code_norm) < 3 or len(code_norm) > 20:
        raise ValueError("code length must be 3..20")
    for ch in code_norm:
        if not (ch.isalnum() or ch in ("_", "-")):
            raise ValueError("code must be alnum/_/-")
    return code_norm


def create_promo(*, amount_ton: float, max_uses: int = 1, code: Optional[str] = None) -> str:
    init_wins_db()
    amt = float(amount_ton)
    if not (amt > 0):
        raise ValueError("amount_ton must be > 0")
    uses = int(max_uses)
    if uses < 1:
        raise ValueError("max_uses must be >= 1")

    conn = get_conn()
    try:
        now = int(time.time())
        if code is not None:
            code_norm = _normalize_promo_code(code)
            conn.execute(
                """
                INSERT INTO promo_codes (code, amount_ton, max_uses, used_count, active, created_ts)
                VALUES (?, ?, ?, 0, 1, ?)
                """,
                (code_norm, amt, uses, now),
            )
            conn.commit()
            return code_norm

        for _ in range(10):
            code_gen = _make_promo_code()
            try:
                conn.execute(
                    """
                    INSERT INTO promo_codes (code, amount_ton, max_uses, used_count, active, created_ts)
                    VALUES (?, ?, ?, 0, 1, ?)
                    """,
                    (code_gen, amt, uses, now),
                )
                conn.commit()
                return code_gen
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("Failed to generate unique promo code")
    finally:
        conn.close()


def list_active_promos(*, limit: int = 50) -> List[Dict[str, Any]]:
    init_wins_db()
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT code, amount_ton, max_uses, used_count, active, created_ts
            FROM promo_codes
            WHERE active = 1 AND used_count < max_uses
            ORDER BY created_ts DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "code": r["code"],
                    "amount_ton": r["amount_ton"],
                    "remaining": int(r["max_uses"] or 0) - int(r["used_count"] or 0),
                }
            )
        return out
    finally:
        conn.close()


def get_balance(*, user_id: int) -> float:
    init_wins_db()
    conn = get_conn()
    try:
        row = conn.execute("SELECT ton_balance FROM wallet WHERE user_id = ?", (int(user_id),)).fetchone()
        if not row:
            return 0.0
        try:
            return float(row["ton_balance"])
        except Exception:
            return 0.0
    finally:
        conn.close()


def redeem_promo(*, code: str, user_id: int) -> float:
    init_wins_db()
    code_norm = str(code or "").strip().upper()
    if not code_norm:
        raise ValueError("code is required")

    conn = get_conn()
    try:
        now = int(time.time())
        conn.execute("BEGIN IMMEDIATE")

        promo = conn.execute(
            """
            SELECT code, amount_ton, max_uses, used_count, active
            FROM promo_codes
            WHERE code = ?
            """,
            (code_norm,),
        ).fetchone()

        if not promo or int(promo["active"] or 0) != 1:
            raise ValueError("Промокод не найден")

        used_count = int(promo["used_count"] or 0)
        max_uses = int(promo["max_uses"] or 0)
        if used_count >= max_uses:
            raise ValueError("Промокод уже закончился")

        # prevent double redeem by same user
        existing = conn.execute(
            "SELECT 1 FROM promo_redemptions WHERE code = ? AND user_id = ?",
            (code_norm, int(user_id)),
        ).fetchone()
        if existing:
            raise ValueError("Ты уже активировал этот промокод")

        amount = float(promo["amount_ton"])
        conn.execute(
            "INSERT INTO promo_redemptions (code, user_id, ts) VALUES (?, ?, ?)",
            (code_norm, int(user_id), now),
        )
        conn.execute(
            "UPDATE promo_codes SET used_count = used_count + 1 WHERE code = ?",
            (code_norm,),
        )
        conn.execute(
            """
            INSERT INTO wallet (user_id, ton_balance, updated_ts)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              ton_balance = wallet.ton_balance + excluded.ton_balance,
              updated_ts = excluded.updated_ts
            """,
            (int(user_id), amount, now),
        )
        conn.commit()
        return amount
    finally:
        conn.close()


def redeem_promo_client(*, code: str, client_id: str) -> float:
    init_wins_db()
    code_norm = str(code or "").strip().upper()
    client_norm = str(client_id or "").strip()
    if not code_norm:
        raise ValueError("code is required")
    if not client_norm:
        raise ValueError("client_id is required")

    conn = get_conn()
    try:
        now = int(time.time())
        conn.execute("BEGIN IMMEDIATE")

        promo = conn.execute(
            """
            SELECT code, amount_ton, max_uses, used_count, active
            FROM promo_codes
            WHERE code = ?
            """,
            (code_norm,),
        ).fetchone()
        if not promo or int(promo["active"] or 0) != 1:
            raise ValueError("Промокод не найден")

        used_count = int(promo["used_count"] or 0)
        max_uses = int(promo["max_uses"] or 0)
        if used_count >= max_uses:
            raise ValueError("Промокод уже закончился")

        existing = conn.execute(
            "SELECT 1 FROM promo_redemptions_clients WHERE code = ? AND client_id = ?",
            (code_norm, client_norm),
        ).fetchone()
        if existing:
            raise ValueError("Ты уже активировал этот промокод")

        amount = float(promo["amount_ton"])
        conn.execute(
            "INSERT INTO promo_redemptions_clients (code, client_id, ts) VALUES (?, ?, ?)",
            (code_norm, client_norm, now),
        )
        conn.execute(
            "UPDATE promo_codes SET used_count = used_count + 1 WHERE code = ?",
            (code_norm,),
        )

        amount_cents = _ton_to_cents(amount)
        row = conn.execute(
            "SELECT ton_balance_cents FROM wallet_clients WHERE client_id = ?",
            (client_norm,),
        ).fetchone()
        current_cents = int(row["ton_balance_cents"]) if row else 0
        new_cents = current_cents + int(amount_cents)
        conn.execute(
            """
            INSERT INTO wallet_clients (client_id, ton_balance_cents, updated_ts)
            VALUES (?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
              ton_balance_cents = excluded.ton_balance_cents,
              updated_ts = excluded.updated_ts
            """,
            (client_norm, int(new_cents), now),
        )

        coins_delta = ton_to_coins(amount)
        rowc = conn.execute(
            "SELECT balance_coins FROM coin_wallet_clients WHERE client_id = ?",
            (client_norm,),
        ).fetchone()
        current_coins = int(rowc["balance_coins"]) if rowc else 0
        new_coins = current_coins + int(coins_delta)
        conn.execute(
            """
            INSERT INTO coin_wallet_clients (client_id, balance_coins, updated_ts)
            VALUES (?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
              balance_coins = excluded.balance_coins,
              updated_ts = excluded.updated_ts
            """,
            (client_norm, int(new_coins), now),
        )
        conn.commit()
        return amount
    finally:
        conn.close()


def set_game_enabled(*, game: str, enabled: bool) -> None:
    init_wins_db()
    conn = get_conn()
    try:
        game_norm = str(game or "").strip().lower()
        if not game_norm:
            raise ValueError("game is required")
        now = int(time.time())
        conn.execute(
            """
            INSERT INTO game_flags (game, enabled, updated_ts)
            VALUES (?, ?, ?)
            ON CONFLICT(game) DO UPDATE SET
              enabled = excluded.enabled,
              updated_ts = excluded.updated_ts
            """,
            (game_norm, 1 if enabled else 0, now),
        )
        conn.commit()
    finally:
        conn.close()


def get_game_flags() -> Dict[str, bool]:
    init_wins_db()
    conn = get_conn()
    try:
        rows = conn.execute("SELECT game, enabled FROM game_flags").fetchall()
        out: Dict[str, bool] = {}
        for r in rows:
            g = str(r["game"] or "").strip().lower()
            if not g:
                continue
            out[g] = bool(int(r["enabled"] or 0))
        return out
    finally:
        conn.close()


def insert_win(
    *,
    ts: Optional[int] = None,
    player_name: Optional[str] = None,
    win_type: str,
    game: Optional[str] = None,
    gift_id: Optional[str] = None,
    gift_name: Optional[str] = None,
    amount_ton: Optional[float] = None,
    meta_json: Optional[str] = None,
) -> int:
    init_wins_db()
    conn = get_conn()
    try:
        ts_val = int(ts if ts is not None else time.time())
        cur = conn.execute(
            """
            INSERT INTO wins (ts, player_name, win_type, game, gift_id, gift_name, amount_ton, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ts_val, player_name, win_type, game, gift_id, gift_name, amount_ton, meta_json),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_wins(*, limit: int = 50) -> List[Dict[str, Any]]:
    init_wins_db()
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, ts, player_name, win_type, game, gift_id, gift_name, amount_ton, meta_json
            FROM wins
            ORDER BY ts DESC, id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
