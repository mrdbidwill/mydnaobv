from __future__ import annotations

import shlex
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
UTILS_PATH = REPO_ROOT / "scripts" / "deploy_alert_utils.sh"


def _run_validation(
    raw_value: str,
    alert_format: str = "plain",
    ntfy_base_url: str = "https://ntfy.sh",
) -> tuple[int, str, str]:
    cmd = (
        f"source {shlex.quote(str(UTILS_PATH))}; "
        "out=''; "
        "reason=''; "
        f"deploy_alert_validate_url {shlex.quote(raw_value)} out reason {shlex.quote(alert_format)} {shlex.quote(ntfy_base_url)}; "
        "rc=$?; "
        "printf '%s\\n%s\\n%s\\n' \"$rc\" \"$out\" \"$reason\""
    )
    result = subprocess.run(
        ["bash", "-lc", cmd],
        check=True,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    lines = result.stdout.splitlines()
    return int(lines[0]), lines[1], lines[2]


def test_validate_url_accepts_and_normalizes_https() -> None:
    rc, value, reason = _run_validation("  https://ntfy.sh/mydnaobv-alerts  ")
    assert rc == 0
    assert value == "https://ntfy.sh/mydnaobv-alerts"
    assert reason == ""


def test_validate_url_rejects_empty() -> None:
    rc, value, reason = _run_validation("   ")
    assert rc == 1
    assert value == ""
    assert reason == "empty"


def test_validate_url_rejects_option_like_value() -> None:
    rc, value, reason = _run_validation("--not-a-url")
    assert rc == 2
    assert value == ""
    assert reason == "starts_with_dash"


def test_validate_url_rejects_whitespace() -> None:
    rc, value, reason = _run_validation("https://ntfy.sh/topic with-space")
    assert rc == 2
    assert value == ""
    assert reason == "contains_whitespace"


def test_validate_url_rejects_non_http_scheme() -> None:
    rc, value, reason = _run_validation("ntfy://topic")
    assert rc == 2
    assert value == ""
    assert reason == "invalid_scheme"


def test_validate_url_accepts_ntfy_topic_shorthand() -> None:
    rc, value, reason = _run_validation("my-topic", alert_format="ntfy", ntfy_base_url="https://ntfy.sh")
    assert rc == 0
    assert value == "https://ntfy.sh/my-topic"
    assert reason == ""
