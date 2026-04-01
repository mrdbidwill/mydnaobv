from __future__ import annotations

import shlex
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
UTILS_PATH = REPO_ROOT / "scripts" / "deploy_env_override_utils.sh"


def _run_capture_restore(before: str, env_file_body: str, var_name: str) -> tuple[str, str]:
    cmd = (
        f"source {shlex.quote(str(UTILS_PATH))}; "
        f"{before}; "
        "tmp_env=$(mktemp); "
        f"cat > \"$tmp_env\" <<'EOF'\n{env_file_body}\nEOF\n"
        f"deploy_env_capture_overrides {shlex.quote(var_name)}; "
        "source \"$tmp_env\"; "
        "deploy_env_restore_overrides; "
        f"printf '%s\\n%s\\n' \"${{{var_name}+set}}\" \"${{{var_name}:-}}\""
    )
    result = subprocess.run(
        ["bash", "-lc", cmd],
        check=True,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    lines = result.stdout.splitlines()
    return lines[0], lines[1]


def test_capture_restore_preserves_explicit_value() -> None:
    is_set, value = _run_capture_restore(
        before="FOO='from-invocation'",
        env_file_body="FOO=from-env-file",
        var_name="FOO",
    )
    assert is_set == "set"
    assert value == "from-invocation"


def test_capture_restore_preserves_explicit_empty_value() -> None:
    is_set, value = _run_capture_restore(
        before="FOO=''",
        env_file_body="FOO=from-env-file",
        var_name="FOO",
    )
    assert is_set == "set"
    assert value == ""


def test_capture_restore_keeps_env_file_default_when_unset() -> None:
    is_set, value = _run_capture_restore(
        before="unset FOO",
        env_file_body="FOO=from-env-file",
        var_name="FOO",
    )
    assert is_set == "set"
    assert value == "from-env-file"
