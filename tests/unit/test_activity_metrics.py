from datetime import date

from app.services.activity import activity_summary, metric_value


def test_streaks_are_clipped_to_year_and_today_can_be_empty():
    result = activity_summary({date(2024, 1, 1): 1, date(2024, 1, 2): 2}, 2024, date(2024, 1, 3))
    assert result == (2, 2, 2)
    assert activity_summary({date(2024, 1, 1): 1}, 2024, date(2024, 1, 3))[1] == 0


def test_measured_volume_uses_snapshot_and_compatible_unit():
    assert metric_value("TIME", None, {"pricing_mode": "PER_TIME", "quantity": 60}) == 60
    assert metric_value("TIME", None, None) == 0
    assert (
        metric_value(
            "QUANTITY", "км", {"pricing_mode": "PER_QUANTITY", "quantity": 4, "unit_label": "км"}
        )
        == 4
    )
    assert (
        metric_value(
            "QUANTITY", "км", {"pricing_mode": "PER_QUANTITY", "quantity": 4, "unit_label": "стр"}
        )
        == 0
    )
