from __future__ import annotations

from copy import deepcopy


DEFAULT_ROI_CONFIG = {
    "supply_price_factor": 0.9,
    "vip_discount_rate": 0.0,
    "general_fee_rate": 0.007,
    "other_fee_rate": 0.02,
    "storage_fee_rate": 0.0,
    "tax_rate": 0.045,
    "management_fee_rate": 0.048,
    "refund_rate": 0.1,
    "refund_flat_fee": 5.0,
    "domestic_shipping_fee": 5.0,
    "gift_cost": 0.0,
    "safe_profit_rate": 0.1,
    "ideal_promotion_ratio": 0.12,
}


def _to_float(value: float | int | str, field_name: str) -> float:
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field_name} 不能为空")
        try:
            result = float(text)
        except ValueError as exc:
            raise ValueError(f"{field_name} 不是有效数字：{value}") from exc
    if result <= 0:
        raise ValueError(f"{field_name} 必须大于 0")
    return result


def calculate_roi(price: float, cost: float, config: dict | None = None) -> dict:
    cfg = deepcopy(DEFAULT_ROI_CONFIG)
    if config:
        cfg.update(config)

    price_value = _to_float(price, "淘系控价")
    cost_value = _to_float(cost, "成本价")
    safe_profit_rate = float(cfg["safe_profit_rate"])
    ideal_promotion_ratio = float(cfg["ideal_promotion_ratio"])
    if ideal_promotion_ratio <= 0:
        raise ValueError("ideal_promotion_ratio 必须大于 0")

    supply_price = price_value * float(cfg["supply_price_factor"])
    vip_fee = price_value * float(cfg["vip_discount_rate"])
    platform_fee = supply_price * (
        float(cfg["general_fee_rate"])
        + float(cfg["other_fee_rate"])
        + float(cfg["storage_fee_rate"])
        + float(cfg["tax_rate"])
        + float(cfg["management_fee_rate"])
    )
    fixed_cost = cost_value + float(cfg["domestic_shipping_fee"]) + float(cfg["gift_cost"])
    refund_loss = float(cfg["refund_rate"]) * float(cfg["refund_flat_fee"])
    operating_profit = supply_price - vip_fee - platform_fee - fixed_cost - refund_loss

    break_even_cpa = max(0.0, operating_profit)
    safe_promotion_fee = max(0.0, operating_profit - price_value * safe_profit_rate)
    ideal_promotion_fee = price_value * ideal_promotion_ratio

    break_even_roi = None if break_even_cpa <= 0 else price_value / break_even_cpa
    safe_roi = None if safe_promotion_fee <= 0 else price_value / safe_promotion_fee
    ideal_roi = price_value / ideal_promotion_fee

    return {
        "break_even_roi": break_even_roi,
        "safe_roi": safe_roi,
        "ideal_roi": ideal_roi,
        "details": {
            "price": price_value,
            "cost": cost_value,
            "supply_price": supply_price,
            "vip_fee": vip_fee,
            "platform_fee": platform_fee,
            "fixed_cost": fixed_cost,
            "refund_loss": refund_loss,
            "operating_profit": operating_profit,
            "break_even_cpa": break_even_cpa,
            "safe_promotion_fee": safe_promotion_fee,
            "ideal_promotion_fee": ideal_promotion_fee,
            "safe_profit_rate": safe_profit_rate,
            "ideal_promotion_ratio": ideal_promotion_ratio,
        },
        "config": cfg,
    }
