from casparser.cli import format_number


def test_format_number():
    assert format_number(100) == "100"
    assert format_number(1000) == "1,000"
