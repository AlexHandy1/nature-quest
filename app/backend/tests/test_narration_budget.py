from datetime import date

from services import narration_budget


def test_allows_calls_under_the_daily_cap():
    allowed = narration_budget.try_consume_daily_budget(date(2026, 8, 5))

    assert allowed is True


def test_rejects_calls_once_the_daily_cap_is_reached():
    today = date(2026, 8, 5)
    for _ in range(narration_budget.DAILY_NARRATION_CALL_CAP):
        narration_budget.try_consume_daily_budget(today)

    allowed = narration_budget.try_consume_daily_budget(today)

    assert allowed is False


def test_resets_the_count_on_a_new_day():
    day_one = date(2026, 8, 5)
    day_two = date(2026, 8, 6)
    for _ in range(narration_budget.DAILY_NARRATION_CALL_CAP):
        narration_budget.try_consume_daily_budget(day_one)

    allowed = narration_budget.try_consume_daily_budget(day_two)

    assert allowed is True
