from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflows.tmcs_sku_roi.roi_calculator import DEFAULT_ROI_CONFIG, calculate_roi


def test_default_roi_config_uses_4_5_percent_tax_rate() -> None:
    assert DEFAULT_ROI_CONFIG["tax_rate"] == pytest.approx(0.045)


def test_calculate_roi_returns_three_values() -> None:
    result = calculate_roi(
        799,
        361,
        config={
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
        },
    )

    assert result["break_even_roi"] is not None
    assert result["safe_roi"] is not None
    assert result["ideal_roi"] == pytest.approx(8.3333333333)


def test_calculate_roi_uses_promotion_ratio_for_ideal_roi() -> None:
    result = calculate_roi(1000, 100, config={"ideal_promotion_ratio": 0.04})

    assert result["ideal_roi"] == pytest.approx(25.0)


def test_calculate_roi_rejects_invalid_price() -> None:
    with pytest.raises(ValueError, match="淘系控价"):
        calculate_roi("", 100)
