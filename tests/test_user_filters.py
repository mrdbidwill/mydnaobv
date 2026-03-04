from app.main import parse_user_filters


def test_parse_user_filters_accepts_numeric_id_only():
    user_id, username, error = parse_user_filters("3924138", "")
    assert error is None
    assert user_id == 3924138
    assert username is None


def test_parse_user_filters_accepts_username_only():
    user_id, username, error = parse_user_filters("", "cabracrazy")
    assert error is None
    assert user_id is None
    assert username == "cabracrazy"


def test_parse_user_filters_requires_one_selector():
    user_id, username, error = parse_user_filters("", "")
    assert user_id is None
    assert username is None
    assert error is not None


def test_parse_user_filters_rejects_bad_numeric_id():
    user_id, username, error = parse_user_filters("abc", "")
    assert user_id is None
    assert username is None
    assert "numeric" in (error or "")


def test_parse_user_filters_rejects_username_with_spaces():
    user_id, username, error = parse_user_filters("", "bad user")
    assert user_id is None
    assert username is None
    assert "cannot contain spaces" in (error or "")
