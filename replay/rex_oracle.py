"""V2 Phase 8 / P3 G14 oracle (design P8.6 5.1.2-5.1.4): independent reproduction of one run from its REX evidence.

INDEPENDENT BY CONSTRUCTION: imports only the Python standard library. It neither imports nor reuses any runtime,
risk, AI, broker, floor or contract module; every rule below is a literal, separately written transcription of the
baseline code, and differential tests compare it with the runtime. It reproduces the runtime's RULES over persisted,
validated data (design 5.1.4 scope): never the provider's inference, the setup detector or the freshness computation,
whose inputs are outside the REX boundary (their observed outputs are taken as the starting point, as stated).

Covered: identity and rule constants; exits (session, provider, data, execution gate, error); F1 (floor status);
ST4 V1 sizing (IEEE-754 in the code's operation order, compared with ``float.hex`` equality) and every Risk gate;
F1b (quantity adapter, Decimal ``ROUND_FLOOR`` semantics); V-P1 / V-P2 / V-P3 (AI response provenance); A1-A4;
H1; P1 (``paper_policy``); the legacy and gated pending-order eligibility; the V1 fill gate clause by clause; the ST8
admission branch; the ST9 re-check and ``submit_plan`` refusal conditions; ST2 position management (legacy newest bar
and every catch-up bar). Every economic write is reproduced from its recorded pre-state and compared with its
recorded post-state (new uuid identities matched by their owning entity).

Fail closed: a missing, malformed, contradictory or unobserved input makes the run FAIL; nothing is inferred to pass.
Reconstructed values (sizing intermediates, adapter transition, fill-gate clause trace) are reported under
``reconstructed`` and never presented as observed.
"""
from datetime import datetime, timezone
from decimal import ROUND_FLOOR, Context, Decimal, localcontext
import hashlib
import json
import math

ORACLE_VERSION = "V2_P3_G14_ORACLE/1"
REX_VERSION = "V2REX/1"
# Literal transcription of the baseline constants (compared with the constants the run recorded).
AI_SCHEMA_VERSION = "1.0"
VALID_AI_STATUSES = frozenset({"OK", "PARTIAL", "NO_DATA", "ERROR"})
VALID_RECOMMENDATIONS = frozenset({"AGREE", "CAUTION", "DISAGREE", "INSUFFICIENT_DATA", "ACCEPT",
                                   "REJECT_RECOMMENDATION"})
VALID_FLOOR_STATUSES = frozenset({"NO_DATA", "NO_SETUP", "WATCH", "AI_CAUTION", "RISK_REJECTED", "PLAN_READY", "ERROR"})
A3_TRIGGERS = ("DISAGREE", "REJECT_RECOMMENDATION")
GATE_BLOCKING_FINAL_STATUSES = frozenset({"AI_CAUTION", "ERROR", "RISK_REJECTED"})
LEGACY_PENDING_BLOCKING = frozenset({"AI_CAUTION", "ERROR", "RISK_REJECTED"})
HEALTHY = frozenset({"OK", "PARTIAL"})
AUTHORIZED_TIMEFRAMES = ("1h", "15m", "5m")
DECLARED_TOLERANCE = Decimal("1e-9")
POLICY_V1 = "V1_FIXED_3R"
RESPONSE_SLOTS = ("ai_structure", "ai_liquidity", "ai_macro", "ai_setup_review", "ai_trade_review")
RESPONSE_FIELDS = ("schema_version", "run_id", "as_of", "symbol", "agent_name", "status", "bias", "confidence",
                   "recommendation", "warnings")
STALE_PAPER_STATE = "STALE_PAPER_STATE"
SKIP_REASON = "skipped_no_data"


def evidence_fingerprint(material):
    """Transcription of ``ai.call_audit.evidence_fingerprint`` over the observed fingerprint material."""
    canonical = json.dumps({"fingerprint_version": material["fingerprint_version"], "symbol": material["symbol"],
                            "agent": material["agent"], "role": material["role"],
                            "evidence": list(material["evidence"])},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def has_usable_evidence(evidence):
    """Transcription of ``ai.runtime.has_usable_evidence``: the provider is called iff a dict item exists."""
    return any(isinstance(item, dict) for item in evidence)


class EvidenceError(Exception):
    """Evidence missing, malformed, contradictory or outside what the oracle reproduces: the run fails."""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


# -- decoding ----------------------------------------------------------------------------------------------------------
def parse_time(text):
    value = datetime.fromisoformat(text)
    if value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceError("NAIVE_TIME", text)
    return value.astimezone(timezone.utc)


def decode(value):
    """REX typed form -> Python values ($f -> float, $d -> Decimal, $t -> aware UTC datetime)."""
    if isinstance(value, list):
        return [decode(item) for item in value]
    if isinstance(value, dict):
        if set(value) == {"$f"}:
            return float.fromhex(value["$f"])
        if set(value) == {"$d"}:
            return Decimal(value["$d"])
        if set(value) == {"$t"}:
            return parse_time(value["$t"])
        return {key: decode(item) for key, item in value.items()}
    return value


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def _aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _time(value):
    """A payload/REX time (ISO text or datetime) as an aware datetime, or None if absent/invalid."""
    if isinstance(value, datetime):
        return value if _aware(value) else None
    if isinstance(value, str):
        try:
            return parse_time(value)
        except (ValueError, EvidenceError):
            return None
    return None


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite(value):
    return _number(value) and math.isfinite(float(value))


def _valid(value):  # broker / trade manager validity: finite non-bool number > 0
    return _finite(value) and float(value) > 0


# -- paper state -------------------------------------------------------------------------------------------------------
class PaperState:
    """Decoded CAS state tuple as recorded: (account, OPEN positions, closed trades, orders, fills)."""

    KEYS = (("positions", "position_id"), ("closed", "trade_id"), ("orders", "order_id"), ("fills", "fill_id"))

    def __init__(self, state):
        if not isinstance(state, list) or len(state) != 5:
            raise EvidenceError("STATE_MALFORMED", "a recorded state must have 5 parts")
        self.account = None if state[0] is None else json.loads(state[0])
        self.positions, self.closed, self.orders, self.fills = {}, {}, {}, {}
        for (name, key), part in zip(self.KEYS, state[1:]):
            target = getattr(self, name)
            for payload in part:
                item = json.loads(payload)
                if item.get(key) in target:
                    raise EvidenceError("STATE_DUPLICATE_ID", item.get(key))
                target[item[key]] = item

    def copy(self):
        clone = PaperState.__new__(PaperState)
        clone.account = None if self.account is None else dict(self.account)
        for name, _ in self.KEYS:
            setattr(clone, name, {k: dict(v) for k, v in getattr(self, name).items()})
        return clone

    def open_by_symbol(self):
        result = {}
        for position in self.positions.values():
            if position["symbol"] in result:
                raise EvidenceError("STATE_DUPLICATE_OPEN_POSITION", position["symbol"])
            result[position["symbol"]] = position
        return result


NEW_ENTITY_MATCH = {"positions": "origin_order_id", "closed": "position_id", "orders": "run_id", "fills": "order_id"}


def compare_states(expected, new_entities, post, where):
    """``expected`` (existing ids, transitioned) plus ``new_entities`` {part: [dict without id]} must equal ``post``."""
    problems = []
    if expected.account != post.account:
        problems.append(f"{where}: account {expected.account} != {post.account}")
    for name, key in PaperState.KEYS:
        exp, got = getattr(expected, name), getattr(post, name)
        for entity_id, item in exp.items():
            if entity_id not in got:
                problems.append(f"{where}: {name} {entity_id} missing after the write")
            elif got[entity_id] != item:
                problems.append(f"{where}: {name} {entity_id} {item} != {got[entity_id]}")
        created = [item for entity_id, item in got.items() if entity_id not in exp]
        wanted = list(new_entities.get(name, ()))
        if len(created) != len(wanted):
            problems.append(f"{where}: {name} created {len(created)} != expected {len(wanted)}")
            continue
        match = NEW_ENTITY_MATCH[name]
        for want in wanted:
            found = [item for item in created if item.get(match) == want.get(match)]
            if len(found) != 1:
                problems.append(f"{where}: {name} new entity for {match}={want.get(match)} not found exactly once")
                continue
            got_item = {k: v for k, v in found[0].items() if k != key}
            if got_item != {k: v for k, v in want.items() if k != key}:
                problems.append(f"{where}: {name} new {got_item} != {want}")
    return problems


# -- transcriptions: ST2 (TradeManager.process_bar) ----------------------------------------------------------------
def manage_bar(state, bar, order):
    """TradeManager.process_bar on ``state`` (mutated) with ``order`` = open-position iteration order (symbols).
    Returns (events, new closed trades)."""
    if not isinstance(bar, dict) or bar.get("timestamp") is None:
        return [], []
    bar_symbol, timestamp = bar.get("symbol"), bar.get("timestamp")
    if bar_symbol is None or not _aware(timestamp) or bar.get("is_closed") is not True:
        return [], []
    open_positions = state.open_by_symbol()
    if sorted(order) != sorted(open_positions):
        raise EvidenceError("OPEN_ORDER_MISMATCH", f"{order} vs {sorted(open_positions)}")
    account = state.account
    events, closed = [], []
    for symbol in list(order):
        position = open_positions[symbol]
        if position["status"] != "OPEN" or position["side"] not in {"LONG", "SHORT"}:
            continue
        opened = _time(position.get("opened_at"))
        if bar_symbol != symbol or opened is None or not _valid(position.get("contract_multiplier")):
            continue
        if timestamp <= opened:
            continue
        if position.get("last_processed_at") is not None:
            processed = _time(position["last_processed_at"])
            if processed is None or timestamp <= processed:
                continue
        open_price, high, low, close = (bar.get(k) for k in ("open", "high", "low", "close"))
        if not all(_valid(v) for v in (open_price, high, low, close)):
            continue
        if not (low <= open_price <= high and low <= close <= high):
            continue
        stop, target = position["stop"], position["target"]
        if position["side"] == "LONG":
            gap_stop, gap_target = open_price <= stop, open_price >= target
            stop_hit, target_hit = low <= stop, high >= target
        else:
            gap_stop, gap_target = open_price >= stop, open_price <= target
            stop_hit, target_hit = high >= stop, low <= target
        exit_price = reason = None
        if gap_stop:
            exit_price, reason = float(open_price), "stop"
        elif gap_target:
            exit_price, reason = float(open_price), "target"
        elif stop_hit:
            exit_price, reason = stop, "stop"
        elif target_hit:
            exit_price, reason = target, "target"
        if exit_price is None:
            position["last_price"] = float(close)
            position["last_processed_at"] = iso(timestamp)
            continue
        entry = position["entry_price"]
        gross = ((exit_price - entry) if position["side"] == "LONG" else (entry - exit_price)) \
            * position["quantity"] * position["contract_multiplier"]
        cost = position["cost_rate"] * position["quantity"] * entry / 100
        net = gross - cost
        trade = {"schema_version": "1.0", "trade_id": None, "position_id": position["position_id"],
                 "run_id": position["run_id"], "symbol": position["symbol"], "side": position["side"],
                 "entry_price": entry, "exit_price": exit_price, "quantity": position["quantity"], "gross_pnl": gross,
                 "cost": cost, "net_pnl": net, "exited_at": iso(bar["timestamp"]), "reason": reason}
        account["realized_pnl"] += net
        closed.append(trade)
        del state.positions[position["position_id"]]
        del open_positions[symbol]
        events += ["STOP_HIT" if reason == "stop" else "TARGET_HIT", "POSITION_CLOSED"]
    unrealized = 0
    for symbol in order:
        if symbol in open_positions:
            unrealized += _unrealized(open_positions[symbol])
    account["unrealized_pnl"] = unrealized
    account["equity"] = account["starting_equity"] + account["realized_pnl"] + account["unrealized_pnl"]
    return events, closed


def _unrealized(position):
    if position.get("last_price") is None or not _valid(position.get("contract_multiplier")):
        return 0.0
    last, entry = position["last_price"], position["entry_price"]
    return ((last - entry) if position["side"] == "LONG" else (entry - last)) \
        * position["quantity"] * position["contract_multiplier"]


# -- transcriptions: ST7 (PaperBroker.process_next_bar, frozen V1 fill gate) ---------------------------------------
def _valid_ohlc(bar):
    values = [bar.get(k) for k in ("open", "high", "low", "close")]
    return all(_valid(v) for v in values) and bar["low"] <= bar["open"] <= bar["high"] \
        and bar["low"] <= bar["close"] <= bar["high"]


def fill_gate(state, order, bar):
    """V1 ``process_next_bar`` on ``order`` (a dict in ``state``, mutated). Returns (events, new fills, new positions,
    clause trace). The trace is RECONSTRUCTED (never observed)."""
    trace = []
    if order is None or order.get("status") != "PENDING":
        return [], [], [], [("status_not_pending", True)]
    if order["side"] not in {"LONG", "SHORT"} or not _valid(order.get("contract_multiplier")):
        order["status"] = "REJECTED"
        return ["ORDER_REJECTED"], [], [], [("invalid_order_contract", True)]
    timestamp = bar.get("timestamp") if isinstance(bar, dict) else None
    as_of = _time(order.get("as_of"))
    if (not isinstance(bar, dict) or bar.get("symbol") != order["symbol"] or not _aware(timestamp)
            or as_of is None or bar.get("is_closed") is not True or not _valid_ohlc(bar)):
        order["status"] = "CANCELLED"
        return ["ORDER_CANCELLED"], [], [], [("invalid_fill_bar", True)]
    if timestamp <= as_of:
        order["status"] = "CANCELLED"
        return ["ORDER_CANCELLED"], [], [], [("bar_not_after_as_of", True)]
    if order.get("risk_policy_version") is not None:
        raise EvidenceError("UNSUPPORTED_STAMPED_ORDER", "V2 risk policies are outside the V1 oracle (design 5.1.7)")
    fill_price = float(bar["open"])
    risk_per_unit = (fill_price - order["stop"]) if order["side"] == "LONG" else (order["stop"] - fill_price)
    reward = (order["target"] - fill_price) if order["side"] == "LONG" else (fill_price - order["target"])
    real_risk = risk_per_unit * order["quantity"] * order["contract_multiplier"]
    current_equity = state.account["equity"]
    submitted = order.get("equity_at_submission")
    clauses = (("current_equity_invalid", lambda: not _valid(current_equity)),
               ("equity_at_submission_invalid", lambda: not _valid(submitted)),
               ("risk_per_unit_not_positive", lambda: risk_per_unit <= 0),
               ("reward_not_positive", lambda: reward <= 0),
               ("fill_rr_below_3", lambda: reward / risk_per_unit < 3),
               ("real_risk_above_1pct_current_equity", lambda: real_risk > current_equity * 0.01),
               ("real_risk_above_1pct_submission_equity", lambda: real_risk > submitted * 0.01))
    rejected = False
    for name, clause in clauses:  # ``or`` short-circuit, in code order
        value = clause()
        trace.append((name, value))
        if value:
            rejected = True
            break
    trace.append(("inputs", {"fill_price": fill_price, "risk_per_unit": risk_per_unit, "reward": reward,
                             "real_risk": real_risk, "current_equity": current_equity}))
    if rejected:
        order["status"] = "REJECTED"
        return ["ORDER_REJECTED"], [], [], trace
    order["status"] = "FILLED"
    fill = {"schema_version": "1.0", "fill_id": None, "order_id": order["order_id"], "run_id": order["run_id"],
            "symbol": order["symbol"], "side": order["side"], "quantity": order["quantity"],
            "planned_entry": order["planned_entry"], "fill_price": fill_price, "fill_timestamp": iso(timestamp)}
    position = {"schema_version": "1.0", "position_id": None, "origin_order_id": order["order_id"],
                "run_id": order["run_id"], "symbol": order["symbol"], "side": order["side"],
                "quantity": order["quantity"], "planned_entry": order["planned_entry"], "entry_price": fill_price,
                "stop": order["stop"], "target": order["target"], "opened_at": iso(timestamp), "status": "OPEN",
                "last_price": fill_price, "contract_multiplier": order["contract_multiplier"],
                "cost_rate": order.get("cost_rate", 0.0), "last_processed_at": None}
    if order.get("setup_id") is not None:
        position["setup_id"] = order["setup_id"]
    return ["ORDER_FILLED", "POSITION_OPENED"], [fill], [position], trace


# -- transcriptions: ST4 (V1 Risk Engine), F1b (adapter), F1, A1-A4, P1, H1 ----------------------------------------
def _to_decimal(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return None
        number = Decimal(repr(float(value))) if isinstance(value, float) else Decimal(value)
    else:
        return None
    if not number.is_finite() or number <= 0:
        return None
    return number


def _geometry(side, entry, stop, target):
    if side not in ("LONG", "SHORT"):
        return None
    values = [_to_decimal(v) for v in (entry, stop, target)]
    if any(v is None for v in values):
        return None
    entry, stop, target = values
    if side == "LONG":
        if not stop < entry < target:
            return None
        risk, reward = entry - stop, target - entry
    else:
        if not target < entry < stop:
            return None
        risk, reward = stop - entry, entry - target
    with localcontext() as context:
        context.prec = 34
        return reward / risk


def _declared_matches(declared, rr):
    if isinstance(declared, bool) or not isinstance(declared, (int, float, Decimal)):
        return False
    value = Decimal(repr(float(declared))) if isinstance(declared, float) else Decimal(declared)
    if not value.is_finite():
        return False
    return abs(value - rr) <= DECLARED_TOLERANCE * max(Decimal(1), abs(rr))


def risk_v1(plan, capital, instrument, config):
    """``evaluar_trade_plan`` for the V1 policy. Returns (decision fields, reconstructed intermediates)."""
    def rejected(symbol, side, entry, stop, target, reason):
        return {"status": "REJECTED", "symbol": symbol, "side": side, "quantity": None, "capital_at_risk": None,
                "entry": entry, "stop": stop, "target": target, "reason": reason, "equity_at_decision": None,
                "risk_fraction": None, "contract_multiplier": None}, {}
    if not isinstance(plan, dict) or instrument is None or not isinstance(config, dict):
        return rejected("", "", 0.0, 0.0, 0.0, "invalid_input")
    try:
        side = str(plan["side"]).upper()
        entry, stop, target = float(plan["entry"]), float(plan["stop"]), float(plan["target"])
        rr = float(plan["risk_reward"])
    except (KeyError, TypeError, ValueError):
        return rejected(plan.get("symbol", ""), plan.get("side", ""), 0.0, 0.0, 0.0, "invalid_plan")
    symbol = plan.get("symbol", "")
    timeframe = plan.get("timeframe")
    if side not in ("LONG", "SHORT") or not (isinstance(timeframe, str) and timeframe in AUTHORIZED_TIMEFRAMES):
        return rejected(symbol, side, entry, stop, target, "invalid_direction_or_timeframe")
    if not all(_finite(v) and v > 0 for v in (entry, stop, target, capital)):
        return rejected(symbol, side, entry, stop, target, "invalid_numeric_value")
    multiplier = instrument.get("contract_multiplier")
    if multiplier is None or not _finite(multiplier) or multiplier <= 0:
        return rejected(symbol, side, entry, stop, target, "instrument_contract_data_unavailable")
    if (side == "LONG" and not stop < entry < target) or (side == "SHORT" and not target < entry < stop):
        return rejected(plan["symbol"], side, entry, stop, target, "invalid_level_order")
    policy = config.get("rr_policy", POLICY_V1)
    if policy != POLICY_V1:
        raise EvidenceError("UNSUPPORTED_RR_POLICY", str(policy))
    if not _finite(rr) or rr < config["ratio_minimo"]:
        return rejected(plan["symbol"], side, entry, stop, target, "rr_below_minimum")
    shape_rr = _geometry(side, entry, stop, target)
    if shape_rr is None:
        return rejected(plan["symbol"], side, entry, stop, target, "invalid_level_order")
    if not _declared_matches(rr, shape_rr):
        return rejected(plan["symbol"], side, entry, stop, target, "rr_declared_mismatch")
    risk_unitario = abs(entry - stop) * multiplier
    risk_money = float(capital) * config["riesgo_por_operacion_pct"] / 100
    capital_max = float(capital) * config["maximo_capital_pct"] / 100
    quantity_risk = risk_money / risk_unitario
    quantity_capital = capital_max / (entry * multiplier)
    quantity = min(quantity_risk, quantity_capital)
    intermediates = {"risk_unitario": risk_unitario, "risk_money": risk_money, "capital_max": capital_max,
                     "quantity_risk": quantity_risk, "quantity_capital": quantity_capital, "quantity": quantity,
                     "declared_rr": rr, "recomputed_rr": str(shape_rr)}
    if quantity <= 0 or not _finite(quantity):
        return rejected(plan["symbol"], side, entry, stop, target, "invalid_quantity")[0], intermediates
    return {"status": "APPROVED", "symbol": plan["symbol"], "side": side, "quantity": float(quantity),
            "capital_at_risk": float(quantity * risk_unitario), "entry": entry, "stop": stop, "target": target,
            "reason": "approved", "equity_at_decision": float(capital),
            "risk_fraction": float(config["riesgo_por_operacion_pct"]) / 100,
            "contract_multiplier": float(multiplier)}, intermediates


ADAPTER_ERRORS = {"ERROR_INVALID_INCREMENT", "ERROR_INVALID_QUANTITY", "ERROR_ROUNDING_INCREASE"}


def quantity_adapter(final_status, decision, step):
    """``apply_paper_quantity_increment``: (outcome label, final_status, decision). Decision is a dict or None."""
    if decision is None or decision.get("status") != "APPROVED":
        return "UNCHANGED_NOT_APPROVED", final_status, decision
    if not _number(step) or not math.isfinite(step) or step <= 0:
        return "ERROR_INVALID_INCREMENT", None, None
    quantity = decision.get("quantity")
    if not _number(quantity) or not math.isfinite(quantity) or quantity <= 0:
        return "ERROR_INVALID_QUANTITY", None, None
    with localcontext(Context()):  # the runtime runs under the default decimal context
        decimal_step = Decimal(str(step))
        units = (Decimal(str(quantity)) / decimal_step).to_integral_value(rounding=ROUND_FLOOR)
        rounded = float(units * decimal_step)
    if rounded <= 0:
        return "REJECTED_BELOW_INCREMENT", "RISK_REJECTED", {**decision, "status": "REJECTED", "quantity": 0.0,
                                                             "capital_at_risk": 0.0,
                                                             "reason": "paper_quantity_below_increment"}
    if rounded > quantity:
        return "ERROR_ROUNDING_INCREASE", None, None
    if rounded == quantity:
        return "UNCHANGED_EXACT", final_status, decision
    capital = decision["capital_at_risk"] * (rounded / quantity)
    return "ROUNDED_DOWN", final_status, {**decision, "quantity": rounded, "capital_at_risk": capital,
                                          "warnings": [*decision.get("warnings", []), "paper_quantity_rounded_down"]}


def floor_status(st4):
    """F1: (final_status, warnings) the floor must have produced from the recorded setup / plan / risk."""
    equity = st4["equity"]
    setup, plan, risk = st4["setup"], st4["trade_plan"], st4["risk_decision"]
    if not _number(equity) or equity <= 0:
        return "NO_SETUP", ["equity_invalid"], None
    if setup is None:
        raise EvidenceError("F1_SETUP_MISSING")
    if setup["status"] in {"NO_SETUP", "WATCH"}:
        return setup["status"], list(setup["warnings"]), None
    if plan is None:
        return "PLAN_UNAVAILABLE", None, None  # warnings depend on the planner's target decision (checked below)
    if risk is None:
        raise EvidenceError("F1_RISK_MISSING")
    return ("PLAN_READY" if risk["status"] == "APPROVED" else "RISK_REJECTED"), list(setup["warnings"]), None


def ai_final_status(report_status, report_warnings, setup_review, trade_review):
    """A1-A4 on the post-adapter report."""
    if "equity_invalid" in report_warnings:
        return "NO_DATA"
    if report_status not in VALID_FLOOR_STATUSES:
        return "ERROR"
    if report_status == "PLAN_READY":
        disagree = setup_review is not None and setup_review.get("recommendation") == "DISAGREE"
        reject = trade_review is not None and trade_review.get("recommendation") == "REJECT_RECOMMENDATION"
        if disagree or reject:
            return "AI_CAUTION"
    return report_status


def paper_policy(final_status, decision, plan_present, responses):
    """P1 with the runtime default ``data_state == CURRENT``."""
    if final_status != "PLAN_READY":
        return False
    if decision is None or decision.get("status") != "APPROVED" or not plan_present:
        return False
    return all(r is not None and r.get("status") in HEALTHY for r in responses)


def ai_healthy(responses4):
    return all(r is not None and r.get("status") in HEALTHY for r in responses4)


def gate_passed(gate):
    return (gate.get("paper_enabled") is True and gate.get("execution_fresh") is True
            and gate.get("session_open") is True and gate.get("ai_healthy") is True
            and isinstance(gate.get("ai_final_status"), str) and gate["ai_final_status"] != ""
            and gate["ai_final_status"] not in GATE_BLOCKING_FINAL_STATUSES)


def submit_plan(account_equity, symbol_open, run_order_ids, run_id, symbol, as_of, final_status, plan, decision,
                instrument):
    """``PaperBroker.submit_plan``: ('NONE', None) | ('EXISTING', order_id) | ('NEW', order dict). Broker inputs:
    the account equity, whether ``symbol`` has an open position, and the ids of orders already carrying ``run_id``."""
    if not _aware(as_of) or final_status != "PLAN_READY":
        return "NONE", None
    if plan is None or decision is None or decision.get("status") != "APPROVED":
        return "NONE", None
    if plan.get("run_id") != run_id or decision.get("symbol") != symbol or decision.get("side") != plan.get("side"):
        return "NONE", None
    if decision.get("side") not in {"LONG", "SHORT"}:
        return "NONE", None
    plan_as_of = plan.get("as_of", as_of)
    if not _aware(plan_as_of):
        return "NONE", None
    if run_order_ids:
        return "EXISTING", run_order_ids[0]
    if not all(_valid(decision.get(k)) for k in ("quantity", "entry", "stop", "target")):
        return "NONE", None
    if instrument is None or not _valid(instrument.get("contract_multiplier")):
        return "NONE", None
    if decision.get("symbol") != instrument.get("symbol"):
        return "NONE", None
    if not _valid(account_equity):
        return "NONE", None
    if symbol_open:
        return "NONE", None
    return "NEW", {"schema_version": "1.0", "order_id": None, "run_id": run_id, "symbol": symbol,
                   "side": decision["side"], "quantity": decision["quantity"], "planned_entry": decision["entry"],
                   "stop": decision["stop"], "target": decision["target"],
                   "contract_multiplier": float(instrument["contract_multiplier"]),
                   "equity_at_submission": float(account_equity), "cost_rate": 0.0,
                   "as_of": iso(plan_as_of), "status": "PENDING"}


# -- the run check -----------------------------------------------------------------------------------------------------
class _Run:
    def __init__(self, run, writes):
        self.raw = run
        self.run = decode(run)
        self.writes = [decode(w) for w in writes]
        self.failures = []
        self.reconstructed = []
        self.checked = []
        self.consumed = set()

    def fail(self, code, detail=""):
        self.failures.append({"code": code, "detail": str(detail)})

    def require(self, condition, code, detail=""):
        if not condition:
            self.fail(code, detail)
        return condition

    def stages(self, name, observer=None):
        found = []
        for stage in self.run["stages"]:
            if stage.get("stage") != name:
                continue
            if observer is False and "observer" in stage:
                continue
            if isinstance(observer, str) and stage.get("observer") != observer:
                continue
            found.append(stage)
        return found

    def one(self, name, required=True):
        found = self.stages(name, observer=False)
        if len(found) > 1:
            raise EvidenceError("STAGE_DUPLICATED", name)
        if not found:
            if required:
                raise EvidenceError("STAGE_MISSING", name)
            return None
        return found[0]

    def writes_in(self, stage):
        return [w for w in self.writes if w["context"].get("stage") == stage]

    def consume(self, write):
        self.consumed.add(write["write_seq"])


def _expect_events(r, write, events, where):
    got = [e["event_type"] for e in (write.get("journal_events") or [])]
    r.require(got == events, "WRITE_EVENTS_MISMATCH", f"{where}: {got} != {events}")


def _check_write_transition(r, write, apply, where):
    """Reproduce one COMMITTED write: ``apply(state)`` -> (events, {part: [new entities]})."""
    r.consume(write)
    if write["result"] != "COMMITTED":
        r.require(write["result"] == "STALE", "WRITE_RAISED", where)
        return None
    pre, post = PaperState(write["pre_state"]), PaperState(write["post_state"])
    expected = pre.copy()
    events, new = apply(expected)
    for problem in compare_states(expected, new, post, where):
        r.fail("TRANSITION_MISMATCH", problem)
    _expect_events(r, write, events, where)
    return post


def _check_management(r, identity):
    r.checked.append("ST2 management")
    if not (identity["catch_up_applies"] or identity.get("snapshot_catch_up_applies", False)):
        inputs = r.stages("ST2L_INPUT", observer=False)
        writes = r.writes_in("ST2L")
        if not inputs:
            r.require(not writes, "UNEXPLAINED_WRITE", "ST2L write without its input")
            return
        for index, write in enumerate(writes):
            context = write["context"]
            bar = context.get("bar")
            if bar is None:
                raise EvidenceError("ST2L_BAR_MISSING")
            source = inputs[min(index, len(inputs) - 1)]
            pre = PaperState(write["pre_state"]) if write.get("pre_state") is not None else None
            if pre is not None:
                r.require(source["had_open"] == bool(pre.positions), "ST2L_HAD_OPEN_MISMATCH")
                r.require(sorted(source["open_positions"]) == sorted(pre.open_by_symbol()), "ST2L_ORDER_MISMATCH")

            def apply(state, bar=bar, order=source["open_positions"]):
                events, closed = manage_bar(state, bar, order) if context.get("paper_enabled") else ([], [])
                return events, {"closed": closed}
            _check_write_transition(r, write, apply, f"ST2L#{write['write_seq']}")
        last = inputs[-1]
        expect_save = bool(last["paper_enabled"]) and bool(last["had_open"])
        if not expect_save:  # a save can still follow a journal event, which needs an open position: none here
            r.require(not writes, "UNEXPECTED_ST2L_WRITE")
        else:
            r.require(bool(writes), "MISSING_ST2L_WRITE", "an open position is always persisted after management")
    else:
        observed = r.stages("ST2C", observer="catch_up_bar")
        writes = r.writes_in("ST2C")
        # V2 P4a: a bar refused at read 2 was observed (before its computation) but never saved: the last one only
        halt = r.one("EXIT_HALT", required=False) if getattr(r, "halted", False) else None
        refused = halt is not None and (halt.get("refused_kind"), halt.get("refused_stage")) == ("catch_up_bar", "read2")
        r.require(len(observed) == len(writes) + refused, "CATCH_UP_BAR_COVERAGE",
                  f"{len(observed)} observed bars, {len(writes)} write entries (NG12)")
        for index, write in enumerate(writes):
            context = write["context"]
            if context.get("observer") != "catch_up_bar" or context.get("bar") is None:
                raise EvidenceError("CATCH_UP_BAR_MISSING", f"write {write['write_seq']}")
            # ordered matching: write i is the save of observed bar i (its write context is that observation), so
            # the single unmatched observation of a read-2 refusal is provably the final bar, never an earlier one
            r.require(index < len(observed) and context == observed[index], "CATCH_UP_BAR_ORDER",
                      f"write {write['write_seq']} is not the save of observed bar {index + 1}")

            def apply(state, context=context):
                events, closed = manage_bar(state, context["bar"], context["open_positions"])
                return events, {"closed": closed}
            _check_write_transition(r, write, apply, f"ST2C#{write['write_seq']}")


def _check_request_observation(r, name, response, entry, requests):
    """The three V-P1 request paths: EMITTED_OBSERVED, LEGITIMATE_OMISSION, or fail (insufficient/contradictory)."""
    run_id, symbol, slot, agent = r.run["run_id"], r.run["symbol"], r.run["slot"], response["agent_name"]
    observed = [o for o in r.run.get("ai_observations") or [] if o.get("agent_name") == agent]
    if not observed:
        raise EvidenceError("VP1_REQUEST_NOT_OBSERVED", f"{name}: no observation of the request")
    if len(observed) > 1:
        raise EvidenceError("VP1_OBSERVATION_DUPLICATED", name)
    obs = observed[0]
    material = obs["fingerprint_material"]
    if not (obs["run_id"] == run_id and obs["symbol"] == symbol and obs["as_of"] == slot
            and obs["schema_version"] == AI_SCHEMA_VERSION and obs["prompt_version"] == entry["prompt_version"]
            and material["symbol"] == obs["symbol"] and material["agent"] == agent
            and material["role"] == obs["role"]):
        raise EvidenceError("VP1_IDENTITY_MISMATCH", f"{name}: observed request identity")
    if evidence_fingerprint(material) != entry.get("evidence_fingerprint"):
        raise EvidenceError("VP1_FINGERPRINT_MISMATCH", name)  # binds the observed request to the audit entry
    usable = has_usable_evidence(material["evidence"])
    if obs["usable_evidence"] is not usable:
        raise EvidenceError("VP1_OBSERVATION_CONTRADICTION", f"{name}: usable_evidence")
    ids = [item.get("evidence_id") for item in material["evidence"] if isinstance(item, dict)]
    if ids != entry["evidence_ids"]:
        raise EvidenceError("VP1_EVIDENCE_IDS_MISMATCH", name)
    probed = [q for q in requests if q["agent_name"] == agent]
    if entry.get("called") is True:
        if not (obs["kind"] == "EMITTED" and obs["provider_called"] is True and obs["reason"] == "emitted"
                and usable):
            raise EvidenceError("VP1_OBSERVATION_CONTRADICTION", f"{name}: emitted path")
        if len(probed) != 1 or any(probed[0][k] != v for k, v in (
                ("run_id", run_id), ("symbol", symbol), ("as_of", slot), ("prompt_version", entry["prompt_version"]),
                ("schema_version", AI_SCHEMA_VERSION))):
            raise EvidenceError("VP1_IDENTITY_MISMATCH", f"{name}: provider-side request")
        return "EMITTED_OBSERVED"
    if entry.get("called") is False:
        if obs["kind"] != "SKIPPED" or obs["provider_called"] is not False or probed:
            raise EvidenceError("VP1_OBSERVATION_CONTRADICTION", f"{name}: skipped path")
        if obs["reason"] != SKIP_REASON or entry.get("validation") != SKIP_REASON:
            raise EvidenceError("VP1_OMISSION_REASON_MISMATCH", name)
        if usable:  # independent re-evaluation of the skip rule over the observed evidence
            raise EvidenceError("VP1_OMISSION_UNJUSTIFIED", f"{name}: usable evidence existed")
        if response["status"] != "NO_DATA" or response["recommendation"] is not None:
            raise EvidenceError("VP2_SUBSTITUTION_INCOHERENT", name)
        return "LEGITIMATE_OMISSION"
    raise EvidenceError("VP1_EVIDENCE_INSUFFICIENT", f"{name}: provider call not recorded")


def _check_provenance(r, st5, identity):
    run_id, symbol, slot = r.run["run_id"], r.run["symbol"], r.run["slot"]
    responses = st5["responses"]
    audit = st5["audit"]
    requests = r.run["ai_requests"]
    valid = {}
    for name in RESPONSE_SLOTS:
        response = responses.get(name)
        if response is None:
            valid[name] = None
            continue
        missing = [f for f in RESPONSE_FIELDS if f not in response]
        if missing:
            raise EvidenceError("RESPONSE_FIELDS_MISSING", f"{name}: {missing}")
        if response["status"] not in VALID_AI_STATUSES:
            raise EvidenceError("RESPONSE_STATUS_INVALID", f"{name}: {response['status']}")
        if response["recommendation"] is not None and response["recommendation"] not in VALID_RECOMMENDATIONS:
            raise EvidenceError("RESPONSE_RECOMMENDATION_INVALID", f"{name}: {response['recommendation']}")
        if not isinstance(response["warnings"], list):
            raise EvidenceError("RESPONSE_WARNINGS_INVALID", name)
        entries = [e for e in audit if e["agent"] == response["agent_name"]]
        if len(entries) != 1:
            raise EvidenceError("PROVENANCE_MISSING", f"{name}: {len(entries)} audit entries")
        entry = entries[0]
        # V-P1: response, audit, the OBSERVED request (emitted or skipped) and the cycle identity bind together
        vp1 = (response["run_id"] == run_id and response["symbol"] == symbol and response["as_of"] == slot
               and response["schema_version"] == AI_SCHEMA_VERSION and entry["run_id"] == run_id
               and entry["agent"] == response["agent_name"])
        if not vp1:
            raise EvidenceError("VP1_IDENTITY_MISMATCH", name)
        path = _check_request_observation(r, name, response, entry, requests)
        r.checked.append(f"V-P1 {name}: {path}")
        audited = entry.get("response")
        if audited is None or any(audited.get(k) != response[k] for k in
                                  ("schema_version", "run_id", "symbol", "as_of", "agent_name", "status",
                                   "recommendation", "bias", "confidence", "warnings")):
            raise EvidenceError("VP1_AUDIT_RESPONSE_MISMATCH", name)
        # V-P2: substitution coherence
        validation = entry.get("validation")
        substituted = validation != "ok"
        if substituted:
            if response["status"] not in {"ERROR", "NO_DATA"} or response["recommendation"] is not None:
                raise EvidenceError("VP2_SUBSTITUTION_INCOHERENT", name)
            if validation == "skipped_no_data" and response["status"] != "NO_DATA":
                raise EvidenceError("VP2_SUBSTITUTION_INCOHERENT", name)
            if validation != "skipped_no_data" and response["status"] != "ERROR":
                raise EvidenceError("VP2_SUBSTITUTION_INCOHERENT", name)
        valid[name] = response  # V-P3: only V-P1/V-P2 responses reach A1-A4, P1 and H1
    r.require(len(audit) == sum(1 for v in valid.values() if v is not None), "PROVENANCE_EXTRA_AUDIT_ENTRY")
    if (valid["ai_trade_review"] is not None) != bool(st5["trade_plan_present"]):
        raise EvidenceError("TRADE_REVIEW_PRESENCE", "the trade reviewer runs iff a plan exists")
    r.checked.append("V-P1/V-P2/V-P3")
    return valid


def _check_run(r):
    run = r.run
    if run.get("rex_version") != REX_VERSION or run.get("kind") != "RUN":
        raise EvidenceError("REX_VERSION")
    if run.get("complete") is not True:
        raise EvidenceError("REX_INCOMPLETE", run.get("failures"))
    identity = run["identity"]
    rules = identity["rule_identity"]
    expected_constants = {"ai_schema_version": AI_SCHEMA_VERSION, "valid_floor_statuses": sorted(VALID_FLOOR_STATUSES),
                          "valid_ai_statuses": sorted(VALID_AI_STATUSES),
                          "valid_recommendations": sorted(VALID_RECOMMENDATIONS), "a3_triggers": list(A3_TRIGGERS),
                          "gate_blocking_final_statuses": sorted(GATE_BLOCKING_FINAL_STATUSES)}
    for key, value in expected_constants.items():
        if rules.get(key) != value:
            raise EvidenceError("RULE_CONSTANTS_MISMATCH", key)
    if not rules.get("files") or any(v is None for v in rules["files"].values()):
        raise EvidenceError("RULE_IDENTITY_INCOMPLETE")
    if identity.get("broker_rr_policy") is not None:
        raise EvidenceError("UNSUPPORTED_RR_POLICY", identity.get("broker_rr_policy"))
    listed = [(w["write_seq"], w["result"]) for w in run["writes"]]
    recorded = [(w["write_seq"], w["result"]) for w in r.writes]
    if listed != recorded:
        raise EvidenceError("RUN_WRITE_LIST_MISMATCH", f"{listed} != {recorded}")
    for write in r.writes:
        if write.get("complete") is not True:
            raise EvidenceError("WRITE_INCOMPLETE", write["write_seq"])
    returned = run["returned"]
    slot = run["slot"]

    failure = r.one("EXIT_ERROR", required=False)
    halt = r.one("EXIT_HALT", required=False)  # V2 P4a: refused admission / lifecycle after T_h
    if failure is not None and halt is not None:
        raise EvidenceError("EXIT_CONTRADICTION", "both EXIT_ERROR and EXIT_HALT")
    error = failure or halt
    terminal = "HALTED" if halt is not None else "ERROR"
    r.halted = halt is not None
    if halt is not None:
        r.require(returned == "HALTED", "HALT_RETURNED_MISMATCH", returned)
        r.checked.append(f"halt: {halt.get('refused_kind')} refused at {halt.get('refused_stage')}")

    session = r.one("EXIT_SESSION", required=False)
    if session is not None:
        sessions = set(session["sessions"])
        slot_out = not set(session["slot_sessions"]) & sessions
        clock_out = session["clock_sessions"] is not None and not set(session["clock_sessions"]) & sessions
        r.require(not identity["diagnostic_outside_session"] and (slot_out or clock_out), "EXIT_SESSION_MISMATCH")
        r.require(returned == ("HALTED" if halt else "SESSION_SKIPPED"), "RETURNED_MISMATCH")
        r.require(not r.writes, "WRITE_AFTER_EXIT")
        return
    for name in ("EXIT_NO_PROVIDER", "EXIT_PROVIDER_ERROR"):
        if r.one(name, required=False) is not None:
            r.require(returned == ("HALTED" if halt else "NO_DATA"), "RETURNED_MISMATCH")
            r.require(not r.writes, "WRITE_AFTER_EXIT")
            return
    st1 = r.one("ST1", required=error is None)
    if st1 is None:  # stopped before ingestion (e.g. a halt right after the claim)
        r.require(returned == terminal and not r.writes, "RETURNED_MISMATCH")
        return
    r.require(st1["catch_up_applies"] == identity["catch_up_applies"], "ST1_SCOPE_MISMATCH")
    data = r.one("DATA", required=error is None)
    if data is None:
        r.require(returned == terminal and not r.writes, "RETURNED_MISMATCH")
        return
    if not data["fresh"]:
        r.require(r.one("EXIT_DATA") is not None and returned == ("HALTED" if halt else data["data_state"]),
                  "EXIT_DATA_MISMATCH")
        r.require(not r.writes, "WRITE_AFTER_EXIT")
        return
    _check_management(r, identity)
    st3 = r.one("ST3", required=error is None)
    if st3 is None:
        r.require(returned == terminal, "RETURNED_MISMATCH")
        return
    r.require(st3["account"]["equity"] == st3["decision_equity"], "ST3_DECISION_EQUITY")
    committed = [w for w in r.writes if w["result"] == "COMMITTED"]
    if committed:
        last_account = PaperState(committed[-1]["post_state"]).account
        r.require(last_account["equity"] == st3["decision_equity"], "ST3_EQUITY_NOT_LAST_STATE")

    st4 = r.one("ST4", required=error is None)
    if st4 is None:
        r.require(returned == terminal, "RETURNED_MISMATCH")
        return
    # F1 and ST4 sizing
    status, warnings, _ = floor_status(st4)
    r.require(st4["final_status"] == status, "F1_STATUS_MISMATCH", f"{st4['final_status']} != {status}")
    if status == "PLAN_UNAVAILABLE":
        r.require(st4["warnings"][:1] == ["plan_unavailable"], "F1_WARNINGS_MISMATCH")
    elif warnings is not None:
        r.require(st4["warnings"] == warnings, "F1_WARNINGS_MISMATCH")
    if st4["trade_plan"] is not None and status in {"PLAN_READY", "RISK_REJECTED"}:
        decision, intermediates = risk_v1(st4["trade_plan"], st4["equity"], st4["instrument"], st4["risk_config"])
        recorded = st4["risk_decision"]
        for key, value in decision.items():
            got = recorded.get(key)
            same = (got == value and type(got) is type(value)) if isinstance(value, float) else got == value
            if isinstance(value, float) and isinstance(got, float):
                same = got.hex() == value.hex()
            r.require(same, "ST4_SIZING_MISMATCH", f"{key}: {got!r} != {value!r}")
        r.reconstructed.append({"stage": "ST4", "sizing_intermediates": {
            k: (v.hex() if isinstance(v, float) else v) for k, v in intermediates.items()}})
        r.checked.append("ST4 V1 sizing (float.hex)")
    r.checked.append("F1")
    # F1b
    f1b = r.one("F1B", required=False)
    outcome, f_status, f_decision = quantity_adapter(st4["final_status"], st4["risk_decision"],
                                                     st4["instrument"].get("quantity_increment")
                                                     if st4["instrument"] else None)
    r.reconstructed.append({"stage": "F1B", "adapter_outcome": outcome})
    if outcome in ADAPTER_ERRORS:
        r.require(f1b is None and failure is not None and returned == "ERROR", "F1B_ERROR_NOT_REPRODUCED", outcome)
        return
    if f1b is None:
        raise EvidenceError("STAGE_MISSING", "F1B")
    r.require(f1b["final_status"] == f_status, "F1B_STATUS_MISMATCH")
    r.require(f1b["risk_decision"] == f_decision, "F1B_DECISION_MISMATCH", f"{f1b['risk_decision']} != {f_decision}")
    r.require(f1b["warnings"] == st4["warnings"], "F1B_WARNINGS_MISMATCH")
    r.checked.append("F1b")
    st5 = r.one("ST5", required=error is None)
    if st5 is None:
        r.require(returned == terminal, "RETURNED_MISMATCH")
        return
    valid = _check_provenance(r, st5, identity)
    final = ai_final_status(f1b["final_status"], f1b["warnings"], valid["ai_setup_review"], valid["ai_trade_review"])
    r.require(st5["final_status"] == final, "A1_A4_MISMATCH", f"{st5['final_status']} != {final}")
    r.checked.append("A1-A4")
    st6 = r.one("ST6", required=error is None)
    if st6 is None:
        r.require(returned == terminal, "RETURNED_MISMATCH")
        return
    if not st6["session_open"]:
        r.require(st6["execution_state"] == "SESSION_SKIPPED" and st6["execution_fresh"] is False,
                  "ST6_SESSION_MISMATCH")
    if not st6["execution_fresh"]:
        r.require(r.one("EXIT_EXECUTION_GATE") is not None and returned == st6["execution_state"],
                  "EXIT_EXECUTION_GATE_MISMATCH")
        r.require(not r.writes_in("ST7") and not r.writes_in("ST9"), "WRITE_AFTER_EXIT")
        return
    four = [valid[n] for n in RESPONSE_SLOTS[:4]]
    healthy = ai_healthy(four)
    st7 = r.one("ST7", required=error is None)
    if st7 is None:
        r.require(returned == terminal, "RETURNED_MISMATCH")
        return
    r.require(st7["ai_healthy"] == healthy, "H1_MISMATCH")
    r.require(st7["ai_healthy_statuses"] == [None if v is None else v["status"] for v in four], "H1_INPUTS_MISMATCH")
    r.require(st7["final_status"] == final, "ST7_FINAL_STATUS")
    r.checked.append("H1")
    if st7["path"] == "LEGACY":
        _check_legacy_pending(r, st7, final, healthy)
    else:
        _check_gated_pending(r, st7, st6, final, healthy, st3)
    st8 = r.one("ST8", required=error is None)
    if st8 is None:
        r.require(returned == terminal, "RETURNED_MISMATCH")
        return
    eligible = paper_policy(final, f1b["risk_decision"], st5["trade_plan_present"],
                            [valid[n] for n in RESPONSE_SLOTS])
    r.require(st8["eligible"] == eligible, "P1_MISMATCH", f"{st8['eligible']} != {eligible}")
    r.checked.append("P1")
    _check_admission(r, st8, st4, f1b, final, eligible, identity, error)
    if error is None:
        r.require(returned == final or returned == "ERROR", "RETURNED_MISMATCH", f"{returned} != {final}")


def _check_legacy_pending(r, st7, final, healthy):
    evaluate = (st7["paper_enabled"] and st7["paper_blocked_before"] is None and healthy
                and final not in LEGACY_PENDING_BLOCKING)
    order_notes = r.stages("ST7_ORDERS", observer=False)
    writes = r.writes_in("ST7")
    if not evaluate:
        r.require(not order_notes and not writes, "ST7_LEGACY_EVALUATED_WITHOUT_ELIGIBILITY")
        return
    r.require(bool(order_notes), "ST7_ORDERS_MISSING")
    for index, write in enumerate(writes):
        note = order_notes[min(index, len(order_notes) - 1)]
        bar = write["context"].get("bar")
        if bar is None:
            raise EvidenceError("ST7_BAR_MISSING")

        def apply(state, note=note, bar=bar):
            r.require(state.account["equity"] == note["current_equity"], "ST7_CURRENT_EQUITY")
            events, fills, positions = [], [], []
            for order_id in note["order_ids"]:
                order = state.orders.get(order_id)
                if order is None:
                    raise EvidenceError("ST7_ORDER_NOT_IN_STATE", order_id)
                if order["symbol"] == r.run["symbol"] and order["status"] == "PENDING" \
                        and bar["timestamp"] > _time(order["as_of"]):
                    e, f, p, trace = fill_gate(state, order, bar)
                    events += e
                    fills += f
                    positions += p
                    if p:
                        state_open = state.open_by_symbol()
                        if p[0]["symbol"] in state_open:
                            raise EvidenceError("FILL_OVER_OPEN_POSITION", p[0]["symbol"])
                    r.reconstructed.append({"stage": "ST7", "order_id": order_id, "fill_gate_trace": [
                        [name, (value.hex() if isinstance(value, float) else value) if not isinstance(value, dict)
                         else {k: (v.hex() if isinstance(v, float) else v) for k, v in value.items()}]
                        for name, value in trace]})
            return events, {"fills": fills, "positions": positions}
        _check_write_transition(r, write, apply, f"ST7#{write['write_seq']}")
    r.checked.append("ST7 legacy fill gate")


def _check_gated_pending(r, st7, st6, final, healthy, st3):
    gate_notes = r.stages("ST7_GATE", observer=False)
    observed = r.stages("ST7", observer="pending_gate") + r.stages("ST7", observer="pending_gate_not_evaluated")
    writes = r.writes_in("ST7")
    if not (st7["paper_enabled"] and st7["paper_blocked_before"] is None):
        r.require(not gate_notes and not observed and not writes, "ST7_GATE_WITHOUT_ELIGIBILITY")
        return
    if not gate_notes:
        r.require(st7["paper_blocked"] == "EVIDENCE_UNAVAILABLE" and not writes, "ST7_GATE_MISSING")
        return
    gate = gate_notes[-1]["gate"]
    expected_gate = {"run_id": r.run["run_id"], "symbol": r.run["symbol"], "paper_enabled": st7["paper_enabled"],
                     "execution_fresh": st6["execution_fresh"], "session_open": st6["session_open"],
                     "ai_healthy": healthy, "ai_final_status": final}
    for key, value in expected_gate.items():
        r.require(gate.get(key) == value, "ST7_GATE_FIELD_MISMATCH", key)
    passed = gate_passed(gate)
    r.require(gate_notes[-1]["gate_passed"] == passed, "ST7_GATE_PASSED_MISMATCH")
    if not observed:
        r.require(not st3["pending_on_symbol"] and not writes, "ST7_GATE_NO_OP_WITH_PENDING")
        return
    for note in observed:
        bar = gate.get("bar")
        timestamp = bar.get("timestamp") if isinstance(bar, dict) else None
        newest = _time(note.get("newest_committed_bar_start"))
        current = None
        if passed and gate["symbol"] == r.run["symbol"] and isinstance(bar, dict) \
                and bar.get("symbol") == r.run["symbol"] and _aware(timestamp) \
                and not (newest is not None and newest > timestamp):
            current = timestamp
        if note["observer"] == "pending_gate_not_evaluated":
            r.require(current is None, "ST7_GATE_NOT_EVALUATED_MISMATCH")
        else:
            r.require(current is not None and note["current"] == current, "ST7_GATE_CURRENT_MISMATCH")
    for write in writes:
        context = write["context"]
        if context.get("observer") != "pending_gate":
            raise EvidenceError("ST7_GATE_CONTEXT_MISSING")

        def apply(state, context=context):
            events, fills, positions = [], [], []
            for order_id in context["order_ids"]:
                order = state.orders.get(order_id)
                if order is None:
                    raise EvidenceError("ST7_ORDER_NOT_IN_STATE", order_id)
                if context["current"] > _time(order["as_of"]):
                    e, f, p, trace = fill_gate(state, order, context["bar"])
                    events, fills, positions = events + e, fills + f, positions + p
                    r.reconstructed.append({"stage": "ST7", "order_id": order_id, "fill_gate_trace": [
                        [n, v if not isinstance(v, (float, dict)) else (v.hex() if isinstance(v, float) else
                                                                        {k: (x.hex() if isinstance(x, float) else x)
                                                                         for k, x in v.items()})]
                        for n, v in trace]})
            return events, {"fills": fills, "positions": positions}
        _check_write_transition(r, write, apply, f"ST7G#{write['write_seq']}")
    r.checked.append("ST7 gated fill gate")


def _check_admission(r, st8, st4, f1b, final, eligible, identity, error):
    st10 = r.one("ST10", required=error is None)
    pending, open_id = st8["pending_on_symbol"], st8["open_on_symbol"]
    submit_path = eligible and st8["paper_enabled"] and st8["paper_blocked"] is None and not pending and open_id is None
    writes = r.writes_in("ST9")
    if not submit_path:
        r.require(not writes and not r.stages("ST9_RECHECK"), "ST9_WITHOUT_ADMISSION")
        if eligible and not st8["paper_enabled"]:
            reason = "PAPER_DIAGNOSTIC_BLOCKED"
        elif eligible and st8["paper_blocked"] is not None:
            reason = st8["paper_blocked"]
        elif final == "AI_CAUTION":
            reason = "AI_CAUTION"
        elif eligible:
            reason = "PENDING_ORDER" if pending else "EXISTING_POSITION"
        else:
            reason = final if final != "PLAN_READY" else "POLICY_REVIEW_UNAVAILABLE"
        if st10 is not None:
            r.require(st10["execution_status"] == "SKIPPED" and st10["execution_reason"] == reason,
                      "ST8_ADMISSION_MISMATCH", f"{st10['execution_reason']} != {reason}")
            if reason == "PENDING_ORDER":
                r.require(st10["blocking_order_id"] in pending, "ST8_BLOCKING_ORDER")
            if reason == "EXISTING_POSITION":
                r.require(st10["blocking_position_id"] == open_id, "ST8_BLOCKING_POSITION")
        r.checked.append("ST8 admission")
        return
    rechecks = r.stages("ST9_RECHECK", observer=False)
    halt = r.one("EXIT_HALT", required=False)
    if not rechecks and not writes and halt is not None and halt.get("refused_kind") == "submit":
        r.checked.append("ST9 refused at the submit admission (halt): no write")
        return
    r.require(bool(rechecks), "ST9_RECHECK_MISSING")
    outcome, order_id = None, None
    for index, write in enumerate(writes):
        recheck = rechecks[min(index, len(rechecks) - 1)]
        refused = recheck["pending_on_symbol"] or recheck["open_on_symbol"] or \
            recheck["equity"] != recheck["decision_equity"]
        r.require(not refused, "ST9_SAVED_DESPITE_RECHECK")

        def apply(state, recheck=recheck):
            nonlocal outcome, order_id
            r.require(state.account["equity"] == recheck["equity"], "ST9_EQUITY_MISMATCH")
            run_orders = [o["order_id"] for o in state.orders.values() if o["run_id"] == r.run["run_id"]]
            r.require(sorted(run_orders) == sorted(recheck["run_order_ids"]), "ST9_RUN_ORDERS_MISMATCH")
            kind, value = submit_plan(state.account["equity"], r.run["symbol"] in state.open_by_symbol(),
                                      recheck["run_order_ids"], r.run["run_id"], r.run["symbol"], r.run["slot"],
                                      f1b["final_status"], st4["trade_plan"], f1b["risk_decision"],
                                      identity["instrument"])
            outcome = kind
            if kind == "NEW":
                return ["ORDER_SUBMITTED"], {"orders": [value]}
            if kind == "EXISTING":
                order_id = value
                return [], {}
            raise EvidenceError("ST9_SAVE_WITHOUT_ORDER")
        post = _check_write_transition(r, write, apply, f"ST9#{write['write_seq']}")
        if post is not None and outcome == "NEW":
            created = [o for o in post.orders.values() if o["run_id"] == r.run["run_id"]]
            order_id = created[0]["order_id"] if len(created) == 1 else None
    if st10 is None:
        return
    if not writes:
        recheck = rechecks[-1]
        refused = recheck["pending_on_symbol"] or recheck["open_on_symbol"] or \
            recheck["equity"] != recheck["decision_equity"]
        if refused:
            expected = ("SKIPPED", STALE_PAPER_STATE)
        else:
            kind, _ = submit_plan(recheck["equity"], recheck["open_on_symbol"], recheck["run_order_ids"],
                                  r.run["run_id"], r.run["symbol"], r.run["slot"], f1b["final_status"],
                                  st4["trade_plan"], f1b["risk_decision"], identity["instrument"])
            r.require(kind == "NONE", "ST9_DECLINE_NOT_REPRODUCED", kind)
            expected = ("SKIPPED", "BROKER_DECLINED_PLAN")
        r.require((st10["execution_status"], st10["execution_reason"]) == expected, "ST9_RESULT_MISMATCH")
    elif writes[-1]["result"] == "COMMITTED":
        r.require((st10["execution_status"], st10["execution_reason"], st10["submitted_order_id"]) ==
                  ("SUBMITTED", "ORDER_SUBMITTED", order_id), "ST9_RESULT_MISMATCH")
    else:
        r.require(st10["execution_reason"] == STALE_PAPER_STATE, "ST9_RESULT_MISMATCH")
    r.checked.append("ST9 submit")


def _check_unconsumed(r):
    for write in r.writes:
        if write["write_seq"] not in r.consumed and getattr(r, "halted", False) and write["result"] == "STALE":
            r.consume(write)  # a halted run: an uncommitted attempt whose retry was refused has no economic effect
            continue
        if write["write_seq"] not in r.consumed:
            r.fail("UNEXPLAINED_WRITE", f"write {write['write_seq']} ({write['context'].get('stage')})")


def check_run(run, writes):
    """G14 for one run. ``run``: the REX_RUN record (as parsed JSON); ``writes``: its REX_WRITE records in write order.
    Returns {"run_id", "result": PASS|FAIL, "failures", "checked", "reconstructed", "oracle_version"}."""
    r = None
    try:
        r = _Run(run, writes)
        _check_run(r)
        _check_unconsumed(r)  # every economic write must have been reproduced by a stage rule
    except EvidenceError as exc:
        r = r or _Run({"run_id": run.get("run_id") if isinstance(run, dict) else None, "stages": []}, [])
        r.fail(exc.code, exc.detail)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError) as exc:
        r = r or _Run({"run_id": None, "stages": []}, [])
        r.fail("EVIDENCE_MALFORMED", f"{type(exc).__name__}: {exc}")
    return {"oracle_version": ORACLE_VERSION, "run_id": r.run.get("run_id") if isinstance(r.run, dict) else None,
            "result": "FAIL" if r.failures else "PASS", "failures": r.failures, "checked": r.checked,
            "reconstructed": r.reconstructed}


__all__ = ["EvidenceError", "ORACLE_VERSION", "PaperState", "ai_final_status", "check_run", "decode", "fill_gate",
           "floor_status", "manage_bar", "paper_policy", "quantity_adapter", "risk_v1", "submit_plan"]
