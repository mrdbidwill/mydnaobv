#!/usr/bin/env python3
"""One-time setup: configure R2 bucket lifecycle rule to expire versioned job artifacts.

Run once on the production server after deploying the HEAD-check / tagging changes:

    cd /opt/mydnaobv
    source venv/bin/activate
    source deploy.env
    python scripts/configure_r2_lifecycle.py

The script is idempotent — safe to re-run.  It installs a single lifecycle rule
("expire-versioned-job-artifacts") that deletes objects tagged
``artifact-type=versioned-job`` after RETENTION_DAYS (default 90).  These are
the per-job versioned copies (list_N/job_N/).  The live latest/ copies are NOT
tagged and are unaffected.

Note: Cloudflare R2 tag-based lifecycle support should be verified in the R2
dashboard after running this script.  If R2 does not honour the tag filter,
delete the rule and use the --prefix mode below instead (requires restructuring
published paths).
"""
from __future__ import annotations

import argparse
import sys


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--retention-days",
        type=int,
        default=90,
        help="Days after creation before versioned job artifacts are deleted (default: 90)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be configured without making any changes",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    from app.exports.publish import configure_r2_lifecycle, publish_enabled
    from app.exports.config import export_config

    if not publish_enabled():
        print("ERROR: publish is not enabled or misconfigured. Check EXPORT_PUBLISH_* env vars.", file=sys.stderr)
        sys.exit(1)

    bucket = str(export_config.publish_bucket or "")
    endpoint = str(export_config.publish_s3_endpoint or "")
    print(f"Target bucket : {bucket}")
    print(f"Endpoint      : {endpoint}")
    print(f"Retention days: {args.retention_days}")
    print(f"Rule ID       : expire-versioned-job-artifacts")
    print(f"Tag filter    : artifact-type=versioned-job")

    if args.dry_run:
        print("\n[dry-run] No changes made.")
        return

    try:
        configure_r2_lifecycle(retention_days=args.retention_days)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print("\nLifecycle rule configured successfully.")
    print("Verify in the Cloudflare R2 dashboard under Settings > Lifecycle rules.")


if __name__ == "__main__":
    main()
