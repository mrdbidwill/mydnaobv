from app.core.config import Settings


def _base_settings(**overrides) -> Settings:
    payload = {
        "DATABASE_URL": "postgresql+psycopg://u:p@localhost:5432/db",
        "ADMIN_USERNAME": "admin_user",
        "ADMIN_PASSWORD": "admin_pass",
    }
    payload.update(overrides)
    return Settings(**payload)


def test_export_operators_json_is_preferred_when_valid():
    cfg = _base_settings(
        EXPORT_OPERATORS_JSON='[{"username":"op1","password":"p1"},{"username":"op2","password":"p2"}]',
        EXPORT_USERNAME="single_user",
        EXPORT_PASSWORD="single_pass",
    )
    assert cfg.export_operator_credentials() == [("op1", "p1"), ("op2", "p2")]


def test_export_operators_json_must_be_array():
    cfg = _base_settings(EXPORT_OPERATORS_JSON='{"username":"op1","password":"p1"}')
    try:
        cfg.export_operator_credentials()
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "JSON array" in str(exc)


def test_single_export_account_is_used_when_no_operator_array():
    cfg = _base_settings(EXPORT_USERNAME="single_user", EXPORT_PASSWORD="single_pass", EXPORT_OPERATORS_JSON="")
    assert cfg.export_operator_credentials() == [("single_user", "single_pass")]


def test_admin_fallback_when_no_export_credentials_present():
    cfg = _base_settings(EXPORT_USERNAME=None, EXPORT_PASSWORD=None, EXPORT_OPERATORS_JSON=None)
    assert cfg.export_operator_credentials() == [("admin_user", "admin_pass")]
