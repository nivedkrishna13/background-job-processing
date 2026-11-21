from app.report import build_report

def test_report_is_repeatable_after_a_retry():
    payload = {"title": "Monthly", "items": ["north", "south"]}
    original = {"title": "Monthly", "items": ["north", "south"]}

    first = build_report(payload)
    second = build_report(payload)

    assert first == second
    assert first["item_count"] == 2
    assert payload == original
