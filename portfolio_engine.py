#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Calculs portfolio exacts en centiemes d'ISK (aucun float)."""
from collections import defaultdict, deque
from datetime import datetime, timezone
import portfolio_metrics as metrics

RISE_BP = 1000
ILLIQUID_DAILY_VOLUME = 10
RATE_SCALE = 1_000_000


def _money(value):
    return str(int(value or 0))


def _fee(amount, rate_ppm):
    return (int(amount) * int(rate_ppm or 0) + RATE_SCALE // 2) // RATE_SCALE


def _trade_fees(row, gross, is_buy):
    actual = row.get("actual_fee_cents")
    if actual is not None:
        return int(actual), "actual"
    broker_rate = int(row.get("broker_rate_ppm") or 0)
    tax_rate = 0 if is_buy else int(row.get("sales_tax_rate_ppm") or 0)
    broker = _fee(gross, broker_rate)
    if broker_rate:
        broker = max(10_000, broker)  # charge courtage minimale CCP: 100 ISK
    return broker + _fee(gross, tax_rate), ("estimated" if broker_rate else "unavailable")


def _consume(lots, quantity):
    wanted, cost, known = int(quantity), 0, 0
    while wanted > 0 and lots:
        lot = lots[0]
        take = min(wanted, lot["quantity"])
        part = (lot["cost_cents"] * take + lot["quantity"] // 2) // lot["quantity"]
        cost += part
        known += take
        lot["cost_cents"] -= part
        lot["quantity"] -= take
        wanted -= take
        if not lot["quantity"]:
            lots.popleft()
    return cost, known, wanted == 0


def _market_metrics(rows):
    usable = [r for r in rows if int(r.get("avg_cents") or 0) > 0][-30:]
    if not usable:
        return {"median_cents": 0, "p75_cents": 0, "daily_volume": 0}
    prices = sorted(int(r["avg_cents"]) for r in usable)
    middle = len(prices) // 2
    median = prices[middle] if len(prices) % 2 else (prices[middle - 1] + prices[middle]) // 2
    p75 = prices[((len(prices) - 1) * 75 + 50) // 100]
    volume = sum(int(r.get("volume") or 0) for r in usable) // len(usable)
    return {"median_cents": median, "p75_cents": p75, "daily_volume": volume}


def _days_running(transactions):
    dates = [str(t.get("date") or "")[:10] for t in transactions if t.get("date")]
    if not dates:
        return 0
    try:
        start = datetime.strptime(min(dates), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return max(1, (datetime.now(timezone.utc) - start).days + 1)
    except ValueError:
        return 0


def _fifo(transactions, contracts):
    lots, realized, rendered = defaultdict(deque), 0, []
    events = []
    for c in contracts:
        qty, cost = int(c.get("quantity") or 0), int(c.get("allocated_cost_cents") or 0)
        if qty > 0 and cost > 0:
            events.append((str(c.get("acquired_at") or ""), 0, "contract", c))
    for tx in transactions:
        events.append((str(tx.get("date") or ""), 1, "transaction", tx))
    for _date, _rank, kind, row in sorted(events, key=lambda value: value[:2]):
        tid = int(row.get("type_id") or 0)
        qty = max(0, int(row.get("quantity") or 0))
        if not tid or not qty:
            continue
        if kind == "contract":
            lots[tid].append({"quantity": qty,
                              "cost_cents": int(row["allocated_cost_cents"]),
                              "source": "contract"})
            continue
        unit = int(row.get("unit_price_cents") or 0)
        gross = unit * qty
        is_buy = bool(row.get("is_buy"))
        if is_buy:
            fees, fee_status = _trade_fees(row, gross, True)
            lots[tid].append({"quantity": qty, "cost_cents": gross + fees,
                              "source": "market"})
            fifo_cost, profit, known = gross + fees, 0, True
        else:
            fees, fee_status = _trade_fees(row, gross, False)
            fifo_cost, consumed_qty, known = _consume(lots[tid], qty)
            profit = gross - fees - fifo_cost if known else 0
            if known:
                realized += profit
        rendered.append({
            "transaction_id": str(row.get("transaction_id") or ""),
            "date": row.get("date") or "", "type_id": str(tid),
            "name": row.get("name") or f"Item #{tid}",
            "side": "BUY" if is_buy else "SELL", "quantity": qty,
            "unit_price_cents": _money(unit), "total_cents": _money(gross),
            "fees_cents": _money(fees), "fifo_cost_cents": _money(fifo_cost),
            "realized_pnl_cents": _money(profit), "cost_known": bool(known),
            "fee_known": fee_status != "unavailable", "fee_status": fee_status,
            "fee_source": "journal" if fee_status == "actual" else row.get("fee_source", ""),
            "broker_rate_ppm": int(row.get("broker_rate_ppm") or 0),
            "sales_tax_rate_ppm": int(row.get("sales_tax_rate_ppm") or 0),
            "consumed_quantity": qty if is_buy else consumed_qty,
            "matched_quantity": 0 if is_buy else consumed_qty,
            "realized_cost_cents": None if is_buy else fifo_cost,
            "realized_profit_cents": profit if not is_buy and known else None,
            "fifo_status": "complete" if is_buy or known else ("partial" if consumed_qty else "unknown_basis"),
            "source": row.get("source_label") or row.get("source_kind") or "",
            "location_id": str(row.get("location_id") or ""),
            "owner_name": row.get("owner_name") or row.get("source_label") or "",
            "station_name": row.get("station_name") or (f"Location #{row.get('location_id')}" if row.get("location_id") else ""),
        })
    return lots, realized, list(reversed(rendered))


def build_workspace(*, transactions=(), assets=(), orders=(), contracts=(),
                    history=None, market=None, cash_cents=0, source_label=""):
    """Construit Dashboard, Assets, Transactions et Alerts depuis une source FIFO."""
    history, market = history or {}, market or {}
    lots, realized, tx_rows = _fifo(transactions, contracts)
    asset_qty, buy_qty, sell_qty = defaultdict(int), defaultdict(int), defaultdict(int)
    buy_value, sell_value = defaultdict(int), defaultdict(int)
    sell_fee_weight, sell_fee_base = defaultdict(int), defaultdict(int)
    names, contract_unallocated = {}, set()
    for item in assets:
        if item.get("is_container"):
            continue
        tid = int(item.get("type_id") or 0)
        if tid:
            asset_qty[tid] += max(0, int(item.get("quantity") or 0))
            names[tid] = item.get("name") or names.get(tid)
    for order in orders:
        tid, qty = int(order.get("type_id") or 0), max(0, int(order.get("volume_remain") or 0))
        price = int(order.get("price_cents") or 0)
        if not tid or not qty:
            continue
        names[tid] = order.get("name") or names.get(tid)
        if bool(order.get("is_buy_order")):
            buy_qty[tid] += qty
            escrow = order.get("escrow_cents")
            buy_value[tid] += int(escrow) if escrow is not None else price * qty
        else:
            sell_qty[tid] += qty
            sell_value[tid] += price * qty
            rate = int(order.get("broker_rate_ppm") or 0) + int(order.get("sales_tax_rate_ppm") or 0)
            if rate:
                sell_fee_weight[tid] += rate * price * qty
                sell_fee_base[tid] += price * qty
    for contract in contracts:
        tid = int(contract.get("type_id") or 0)
        if tid:
            names[tid] = contract.get("name") or names.get(tid)
            if int(contract.get("allocated_cost_cents") or 0) <= 0:
                contract_unallocated.add(tid)
    types = set(asset_qty) | set(buy_qty) | set(sell_qty) | set(lots) | set(contract_unallocated)
    alerts, rows, inventory_value, unrealized = [], [], 0, 0
    for tid in sorted(types):
        metric = dict(_market_metrics(history.get(tid, history.get(str(tid), []))))
        metric.update({k: int(v or 0) for k, v in market.get(tid, market.get(str(tid), {})).items()})
        median = metric.get("current_sell_cents") or metric.get("median_cents") or 0
        daily_volume = metric.get("daily_volume") or 0
        queue = lots.get(tid, ())
        lot_sources = {lot.get("source") for lot in queue}
        lot_qty = sum(lot["quantity"] for lot in queue)
        contract_qty = sum(lot["quantity"] for lot in queue if lot["source"] == "contract")
        held = max(asset_qty[tid], sell_qty[tid], contract_qty)
        known_qty = min(held, lot_qty)
        lot_cost = sum(lot["cost_cents"] for lot in queue)
        avg_cost = (lot_cost + lot_qty // 2) // lot_qty if lot_qty else 0
        cost_basis = avg_cost * known_qty
        valuation_price = median or (avg_cost if contract_qty else 0)
        listed = min(held, sell_qty[tid])
        listed_value = (sell_value[tid] * listed // sell_qty[tid]) if sell_qty[tid] else 0
        value = listed_value + max(0, held - listed) * valuation_price
        gross_market = held * valuation_price
        latent = value - cost_basis if known_qty == held else 0
        sell_rate = (sell_fee_weight[tid] // sell_fee_base[tid]) if sell_fee_base[tid] else int(metric.get("sell_fee_rate_ppm") or 0)
        projected = gross_market - _fee(gross_market, sell_rate) - cost_basis if known_qty == held else 0
        margin_bp = projected * 10000 // gross_market if gross_market and known_qty == held else 0
        net_listed_value = sell_value[tid] - _fee(sell_value[tid], sell_rate)
        under_cost = bool(avg_cost and sell_qty[tid] and
                          net_listed_value < avg_cost * sell_qty[tid])
        unlisted = bool(held and not sell_qty[tid])
        illiquid = bool(held and daily_volume < ILLIQUID_DAILY_VOLUME)
        rising = bool(held and avg_cost and median and
                      median * 10000 >= avg_cost * (10000 + RISE_BP))
        action = "HOLD" if under_cost else "LIST" if unlisted else "SELL" if rising else "RISK" if illiquid else "WATCH"
        name = names.get(tid) or f"Item #{tid}"
        if under_cost:
            alerts.append(_alert(0, "HOLD", tid, name,
                                 "Vente nette de frais sous le coût FIFO",
                                 avg_cost * sell_qty[tid] - net_listed_value))
        if unlisted:
            alerts.append(_alert(1, "LIST", tid, name, "Stock détenu sans ordre de vente", value))
        if illiquid:
            alerts.append(_alert(2, "RISK", tid, name, f"Liquidité faible : {daily_volume}/jour", value))
        if rising:
            alerts.append(_alert(1, "SELL", tid, name, "Marché au-dessus du coût FIFO +10 %", projected))
        if tid in contract_unallocated or (contract_qty and not median):
            alerts.append(_alert(1, "REVALUE", tid, name, "Coût contrat conservé malgré l'absence de prix marché", lot_cost))
        inventory_value += value
        unrealized += latent
        rows.append({
            "type_id": str(tid), "name": name, "quantity": held,
            "asset_qty": asset_qty[tid], "sell_qty": sell_qty[tid], "buy_qty": buy_qty[tid],
            "avg_cost_cents": _money(avg_cost), "market_median_cents": _money(median),
            "valuation_price_cents": _money(valuation_price),
            "market_p75_cents": _money(metric.get("p75_cents") or 0),
            "inventory_value_cents": _money(value), "cost_basis_cents": _money(cost_basis),
            "unrealized_pnl_cents": _money(latent), "projected_profit_cents": _money(projected),
            "buy_exposure_cents": _money(buy_value[tid]), "margin_bp": margin_bp,
            "daily_volume": daily_volume, "cost_known": known_qty == held,
            "sell_fee_rate_ppm": sell_rate, "fee_known": bool(sell_rate), "action": action,
            "acquisition_source": ("Contrat" if lot_sources == {"contract"} else
                                   "Marché + contrat" if "contract" in lot_sources else
                                   "Marché" if lot_sources else "Inconnue"),
        })
    rows.sort(key=lambda row: int(row["projected_profit_cents"]), reverse=True)
    alerts.sort(key=lambda row: (row.pop("_rank"), -abs(int(row["value_cents"]))))
    escrow = sum(buy_value.values())
    summary = {"cash_cents": _money(cash_cents), "inventory_value_cents": _money(inventory_value),
               "buy_escrow_cents": _money(escrow),
               "fund_value_cents": _money(int(cash_cents) + inventory_value + escrow),
               "realized_pnl_cents": _money(realized), "unrealized_pnl_cents": _money(unrealized),
               "days_running": _days_running(transactions),
               "monthly_return_bp": metrics.trailing_realized_return_bp(tx_rows)}
    return {"ok": True, "source_label": source_label, "summary": summary,
            "assets": rows, "transactions": tx_rows, "alerts": alerts,
            "winners": rows[:3], "losers": sorted(rows, key=lambda r: int(r["projected_profit_cents"]))[:3]}


def _alert(rank, action, type_id, name, message, value):
    return {"_rank": rank, "severity": ("critical", "warning", "info")[rank],
            "action": action, "type_id": str(type_id), "name": name,
            "message": message, "value_cents": _money(value)}
