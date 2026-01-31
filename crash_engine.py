import json
import threading
import time
import secrets
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from game_config import cfg
from wins_db import get_app_setting, init_wins_db, set_app_setting


def _now_ms() -> int:
    return int(time.time() * 1000)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _rand_float() -> float:
    # secrets-based uniform [0,1)
    return secrets.randbelow(10**9) / 10**9


@dataclass
class _Bet:
    client_id: str
    amount_coins: int
    placed_ms: int
    cashed_out: bool = False
    cashout_multiplier: Optional[float] = None


class CrashEngine:
    """Adaptive crash engine.

    - No pre-generated crash point.
    - Each tick recomputes hazard (crash probability for the tick) based on live game state.
    - A secrets-based random draw decides whether crash occurs *this tick*.

    Persistence:
    - bankroll and lastRoundsHistory stored in app_settings.

    NOTE: This engine only manages game state. Wallet debits/credits are done in API handlers.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        self.round_id = 0
        self.round_status = "idle"  # idle|running|crashed|cooldown
        self.round_started_ms = 0
        self.round_crashed_ms = 0
        self.multiplier = 1.0

        self._bets: Dict[str, _Bet] = {}
        self._idems_seen: Dict[str, int] = {}

        self._last_tick_ms = 0

        self.bankroll_coins = 0
        self.last_rounds_history: List[float] = []

        self.last_hazard_info: Dict[str, Any] = {}
        self.last_crash_reason: Dict[str, Any] = {}

        self._no_bets_target_range: Tuple[float, float] = (8.0, 50.0)
        self._no_bets_target_pick: float = 12.0

        self._load_persisted()
        self._start_new_round(reason="boot")

    def _load_persisted(self) -> None:
        init_wins_db()
        try:
            b = get_app_setting(key="crash.bankroll_coins", default=None)
            if b is not None:
                self.bankroll_coins = int(float(str(b).strip()))
        except Exception:
            self.bankroll_coins = 0

        if self.bankroll_coins <= 0:
            self.bankroll_coins = int(cfg("crash.bankroll.initial_bankroll_coins", 2500000) or 2500000)

        try:
            raw = get_app_setting(key="crash.last_rounds_history", default=None)
            if raw:
                arr = json.loads(raw)
                if isinstance(arr, list):
                    out: List[float] = []
                    for x in arr[-50:]:
                        try:
                            out.append(float(x))
                        except Exception:
                            pass
                    self.last_rounds_history = out
        except Exception:
            self.last_rounds_history = []

    def _persist(self) -> None:
        try:
            set_app_setting(key="crash.bankroll_coins", value=str(int(self.bankroll_coins)))
        except Exception:
            pass
        try:
            max_hist = int(cfg("crash.history.max_rounds", 20) or 20)
            arr = self.last_rounds_history[-max_hist:]
            set_app_setting(key="crash.last_rounds_history", value=json.dumps(arr, ensure_ascii=False))
        except Exception:
            pass

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            t = threading.Thread(target=self._loop, daemon=True)
            self._thread = t
            t.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False

    def _loop(self) -> None:
        tick_ms = int(cfg("crash.tick_ms", 200) or 200)
        while True:
            with self._lock:
                if not self._running:
                    break
            try:
                self.tick()
            except Exception:
                pass
            time.sleep(max(0.01, tick_ms / 1000.0))

    def _start_new_round(self, *, reason: str) -> None:
        self.round_id += 1
        self.round_status = "running"
        self.round_started_ms = _now_ms()
        self.round_crashed_ms = 0
        self.multiplier = 1.0
        self._bets = {}
        self._idems_seen = {}
        self._last_tick_ms = self.round_started_ms

        rng = cfg("crash.no_bets_mode.target_high_range", [8.0, 50.0]) or [8.0, 50.0]
        try:
            lo = float(rng[0])
            hi = float(rng[1])
        except Exception:
            lo, hi = 8.0, 50.0
        if hi < lo:
            lo, hi = hi, lo
        self._no_bets_target_range = (max(1.5, lo), max(2.0, hi))
        # pick a "missed" target multiplier for no-bets illusion
        self._no_bets_target_pick = self._no_bets_target_range[0] + _rand_float() * (
            self._no_bets_target_range[1] - self._no_bets_target_range[0]
        )

    def _finish_round(self, *, crash_multiplier: float) -> None:
        self.round_status = "crashed"
        self.round_crashed_ms = _now_ms()
        self.multiplier = float(max(1.0, crash_multiplier))

        self.last_rounds_history.append(self.multiplier)
        max_hist = int(cfg("crash.history.max_rounds", 20) or 20)
        if len(self.last_rounds_history) > max_hist:
            self.last_rounds_history = self.last_rounds_history[-max_hist:]

        self._persist()

        # short cooldown to let clients animate crash
        self.round_status = "cooldown"

    def _cooldown_done(self) -> bool:
        # 2s cooldown
        return (_now_ms() - self.round_crashed_ms) >= 2000

    def tick(self) -> None:
        with self._lock:
            if self.round_status == "cooldown":
                if self._cooldown_done():
                    self._start_new_round(reason="cooldown")
                return

            if self.round_status != "running":
                self._start_new_round(reason="reset")
                return

            now = _now_ms()
            dt_ms = max(1, now - self._last_tick_ms)
            self._last_tick_ms = now

            # multiplier growth (simple exponential-ish via per-second additive on log scale)
            growth_per_sec = float(cfg("crash.multiplier_growth_per_sec", 0.24) or 0.24)
            dt_s = dt_ms / 1000.0
            self.multiplier *= (1.0 + growth_per_sec * dt_s)
            self.multiplier = min(self.multiplier, float(cfg("crash.max_multiplier_soft", 200.0) or 200.0))

            hazard, info = self._compute_hazard_with_breakdown(dt_s=dt_s)
            self.last_hazard_info = info

            # Per-tick crash draw
            roll = _rand_float()
            if roll < hazard:
                self.last_crash_reason = {
                    "round_id": int(self.round_id),
                    "multiplier": float(self.multiplier),
                    "hazard": float(hazard),
                    "roll": float(roll),
                    "info": info,
                }
                self._finish_round(crash_multiplier=self.multiplier)

    def _compute_hazard(self, *, dt_s: float) -> float:
        hazard, _ = self._compute_hazard_with_breakdown(dt_s=dt_s)
        return hazard

    def _compute_hazard_with_breakdown(self, *, dt_s: float) -> Tuple[float, Dict[str, Any]]:
        active_players = len(self._bets)
        cashed_out = 0
        total_bets = 0
        hold_sum_s = 0.0
        holding_players = 0

        now = _now_ms()
        for b in self._bets.values():
            if b.cashed_out:
                cashed_out += 1
                continue
            total_bets += int(b.amount_coins)
            hold_sum_s += max(0.0, (now - int(b.placed_ms)) / 1000.0)
            holding_players += 1

        avg_hold_s = (hold_sum_s / holding_players) if holding_players > 0 else 0.0

        info: Dict[str, Any] = {
            "active_players": int(active_players),
            "cashed_out_players": int(cashed_out),
            "total_bets_coins": int(total_bets),
            "avg_hold_s": float(avg_hold_s),
            "bankroll_coins": int(self.bankroll_coins),
            "multiplier": float(self.multiplier),
        }

        # Rule 1: no bets OR all cashed out => allow higher multipliers often
        if active_players == 0 or active_players == cashed_out:
            base = float(cfg("crash.hazard.base", 0.00035) or 0.00035)
            hz_mult = float(cfg("crash.no_bets_mode.hazard_mult", 0.25) or 0.25)

            # keep hazard very low until we reach the picked "missed" target
            if self.multiplier < self._no_bets_target_pick:
                hazard = base * hz_mult * 0.35
            else:
                # once we passed target, allow crash to happen soon
                hazard = base * hz_mult * 2.2

            hazard_clamped = _clamp(
                hazard,
                float(cfg("crash.hazard.min", 0.00005) or 0.00005),
                float(cfg("crash.hazard.max", 0.35) or 0.35),
            )
            info.update(
                {
                    "mode": "no_bets",
                    "no_bets_target": float(self._no_bets_target_pick),
                    "base": float(base),
                    "no_bets_hazard_mult": float(hz_mult),
                    "hazard_raw": float(hazard),
                    "hazard": float(hazard_clamped),
                }
            )
            return hazard_clamped, info

        # Active bets present: adaptive hazard
        base = float(cfg("crash.hazard.base", 0.00035) or 0.00035)
        time_factor = float(cfg("crash.hazard.time_factor", 1.08) or 1.08)
        mult_factor = float(cfg("crash.hazard.multiplier_factor", 1.06) or 1.06)

        elapsed_s = max(0.0, (now - self.round_started_ms) / 1000.0)

        # Core growth: increasing chance over time and multiplier
        hazard = base
        hazard *= (time_factor ** max(0.0, elapsed_s))
        hazard *= (mult_factor ** max(0.0, self.multiplier - 1.0))
        base_time_risk = float(hazard)

        # Exposure: compare potential payout vs allowed RTP
        allowed_rtp = float(cfg("crash.exposure.allowed_rtp", 0.93) or 0.93)
        potential_payout = 0.0
        for b in self._bets.values():
            if b.cashed_out:
                continue
            potential_payout += float(b.amount_coins) * float(self.multiplier)

        # approximate "expected" liabilities vs collected
        collected = float(total_bets)
        allowed_liability = max(1.0, collected * allowed_rtp)
        exposure_ratio = potential_payout / allowed_liability

        exposure_mult = 1.0
        if exposure_ratio > 1.0:
            exposure_mult *= 1.0 + (exposure_ratio - 1.0) * float(cfg("crash.exposure.exposure_to_hazard_mult", 2.5) or 2.5)

        # Greed signal: many players still holding while multiplier grows
        greedy_ratio = holding_players / max(1, active_players)
        greed_mult = 1.0 + greedy_ratio * float(cfg("crash.exposure.greed_to_hazard_mult", 1.8) or 1.8)

        # Hold time signal
        hold_baseline = float(cfg("crash.exposure.hold_baseline_s", 2.5) or 2.5)
        hold_mult = 1.0
        if avg_hold_s > hold_baseline:
            hold_mult *= 1.0 + ((avg_hold_s - hold_baseline) / hold_baseline) * float(
                cfg("crash.exposure.hold_time_to_hazard_mult", 1.4) or 1.4
            )

        # Bankroll control
        min_reserve_ratio = float(cfg("crash.bankroll.min_reserve_ratio", 0.35) or 0.35)
        payout_cap_ratio = float(cfg("crash.bankroll.payout_cap_ratio", 0.55) or 0.55)
        reserve = float(self.bankroll_coins) * min_reserve_ratio
        payout_cap = float(self.bankroll_coins) * payout_cap_ratio

        bank_mult = 1.0
        if potential_payout > payout_cap:
            bank_mult *= float(cfg("crash.bankroll.low_bankroll_hazard_mult", 1.65) or 1.65)
        elif potential_payout < max(1.0, reserve * 0.6):
            bank_mult *= float(cfg("crash.bankroll.high_bankroll_hazard_mult", 0.90) or 0.90)

        hazard *= exposure_mult * greed_mult * hold_mult * bank_mult

        hazard_clamped = _clamp(
            hazard,
            float(cfg("crash.hazard.min", 0.00005) or 0.00005),
            float(cfg("crash.hazard.max", 0.35) or 0.35),
        )
        info.update(
            {
                "mode": "risk",
                "elapsed_s": float(elapsed_s),
                "base": float(base),
                "time_factor": float(time_factor),
                "mult_factor": float(mult_factor),
                "base_time_risk": float(base_time_risk),
                "potential_payout_coins": float(potential_payout),
                "allowed_rtp": float(allowed_rtp),
                "allowed_liability_coins": float(allowed_liability),
                "exposure_ratio": float(exposure_ratio),
                "exposure_mult": float(exposure_mult),
                "greedy_ratio": float(greedy_ratio),
                "greed_mult": float(greed_mult),
                "hold_baseline_s": float(hold_baseline),
                "hold_mult": float(hold_mult),
                "min_reserve_ratio": float(min_reserve_ratio),
                "payout_cap_ratio": float(payout_cap_ratio),
                "reserve_coins": float(reserve),
                "payout_cap_coins": float(payout_cap),
                "bank_mult": float(bank_mult),
                "hazard_raw": float(hazard),
                "hazard": float(hazard_clamped),
            }
        )
        return hazard_clamped, info

    def place_bet(self, *, client_id: str, amount_coins: int, idempotency_key: str) -> Dict[str, Any]:
        with self._lock:
            if self.round_status != "running":
                raise ValueError("round not running")
            if amount_coins <= 0:
                raise ValueError("amount_coins must be > 0")

            idem = str(idempotency_key or "").strip()
            if not idem:
                raise ValueError("idempotency_key is required")
            if idem in self._idems_seen:
                return {"ok": True, "duplicate": True, "round_id": self.round_id}
            self._idems_seen[idem] = _now_ms()

            cid = str(client_id or "").strip()
            if not cid:
                raise ValueError("client_id is required")

            if cid in self._bets and not self._bets[cid].cashed_out:
                raise ValueError("bet already active")

            self._bets[cid] = _Bet(client_id=cid, amount_coins=int(amount_coins), placed_ms=_now_ms())
            return {"ok": True, "round_id": self.round_id, "multiplier": float(self.multiplier)}

    def cashout(self, *, client_id: str, idempotency_key: str) -> Dict[str, Any]:
        with self._lock:
            if self.round_status != "running":
                raise ValueError("round not running")

            idem = str(idempotency_key or "").strip()
            if not idem:
                raise ValueError("idempotency_key is required")
            if idem in self._idems_seen:
                return {"ok": True, "duplicate": True, "round_id": self.round_id}
            self._idems_seen[idem] = _now_ms()

            cid = str(client_id or "").strip()
            if not cid:
                raise ValueError("client_id is required")

            b = self._bets.get(cid)
            if not b or b.cashed_out:
                raise ValueError("no active bet")

            b.cashed_out = True
            b.cashout_multiplier = float(self.multiplier)

            payout = int(round(float(b.amount_coins) * float(self.multiplier)))
            return {
                "ok": True,
                "round_id": self.round_id,
                "cashout_multiplier": float(b.cashout_multiplier),
                "payout_coins": int(payout),
            }

    def get_public_state(self) -> Dict[str, Any]:
        with self._lock:
            active_players = len(self._bets)
            cashed_out = 0
            total_bets = 0
            for b in self._bets.values():
                total_bets += int(b.amount_coins)
                if b.cashed_out:
                    cashed_out += 1

            return {
                "round_id": int(self.round_id),
                "status": str(self.round_status),
                "started_ms": int(self.round_started_ms),
                "crashed_ms": int(self.round_crashed_ms),
                "multiplier": float(self.multiplier),
                "active_players": int(active_players),
                "cashed_out_players": int(cashed_out),
                "active_bets_amount_coins": int(total_bets),
                "bankroll_coins": int(self.bankroll_coins),
                "last_rounds_history": list(self.last_rounds_history),
                "last_hazard": dict(self.last_hazard_info),
                "last_crash_reason": dict(self.last_crash_reason),
            }

    def bankroll_on_bet(self, *, amount_coins: int) -> None:
        with self._lock:
            self.bankroll_coins += int(max(0, amount_coins))
            self._persist()

    def bankroll_on_payout(self, *, payout_coins: int) -> None:
        with self._lock:
            self.bankroll_coins -= int(max(0, payout_coins))
            # never allow negative (for safety); in real money you should halt game.
            if self.bankroll_coins < 0:
                self.bankroll_coins = 0
            self._persist()


_ENGINE: Optional[CrashEngine] = None


def get_crash_engine() -> CrashEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = CrashEngine()
        _ENGINE.start()
    return _ENGINE
