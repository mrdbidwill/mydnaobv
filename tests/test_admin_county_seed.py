from app.main import parse_optional_user_filters, parse_project_filter
from app.services.us_counties import normalize_state_code


def test_parse_optional_user_filters_allows_empty():
    user_id, username, error = parse_optional_user_filters("", "")
    assert error is None
    assert user_id is None
    assert username is None


def test_parse_optional_user_filters_rejects_invalid_numeric_id():
    user_id, username, error = parse_optional_user_filters("not-a-number", "")
    assert user_id is None
    assert username is None
    assert "numeric" in (error or "")


def test_parse_project_filter_accepts_slug():
    project_id, error = parse_project_filter("fungi-of-alabama-ams-fundis-local-project")
    assert error is None
    assert project_id == "fungi-of-alabama-ams-fundis-local-project"


def test_parse_project_filter_rejects_spaces():
    project_id, error = parse_project_filter("bad project")
    assert project_id is None
    assert "cannot contain spaces" in (error or "")


def test_normalize_state_code():
    assert normalize_state_code("al") == "AL"
    assert normalize_state_code(" ZZ ") is None
