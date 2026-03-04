from datetime import datetime

from app.exports.config import export_config
from app.exports.policy import evaluate_license


def test_license_allowlist_accepts_common_creative_commons_codes():
    assert "cc-by" in export_config.allowed_licenses
    decision = evaluate_license("CC-BY")
    assert decision.allowed is True


def test_license_policy_blocks_missing_license_by_default():
    decision = evaluate_license(None)
    assert decision.allowed is False
    assert decision.reason == "missing_license"


def test_license_policy_blocks_explicitly_restricted_values():
    decision = evaluate_license("copyright")
    assert decision.allowed is False
    assert decision.reason == "explicitly_restricted"


def test_large_window_default_is_overnight():
    day_time = datetime(2026, 3, 4, 12, 0, 0)
    overnight = datetime(2026, 3, 4, 1, 0, 0)
    assert export_config.is_large_window_open(day_time) is False
    assert export_config.is_large_window_open(overnight) is True
