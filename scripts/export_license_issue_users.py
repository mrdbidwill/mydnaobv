#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a unique user list from problem_observations CSV, focused on "
            "license-restricted rows for outreach."
        )
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Input CSV from export_problem_observations.py",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output CSV for unique users",
    )
    parser.add_argument(
        "--include-all-issues",
        action="store_true",
        help="Include non-license issues too (default is license_restricted only).",
    )
    parser.add_argument(
        "--max-sample-observations",
        type=int,
        default=5,
        help="Max sample observation URLs per user (default: 5).",
    )
    return parser


def _normalize_observer(value: str | None) -> str:
    candidate = (value or "").strip()
    if candidate:
        return candidate
    return "(unknown)"


def _host_path(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        return f"{parsed.netloc}{parsed.path}"
    except Exception:
        return url


def main() -> int:
    args = build_parser().parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    max_samples = max(1, int(args.max_sample_observations))

    if not input_path.exists():
        raise SystemExit(f"Input CSV not found: {input_path}")

    stats: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "issue_rows": 0,
            "observations": set(),
            "counties": set(),
            "issue_types": set(),
            "sample_urls": [],
        }
    )

    with input_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            issue_type = (row.get("issue_type") or "").strip()
            if not args.include_all_issues and issue_type != "license_restricted":
                continue

            observer = _normalize_observer(row.get("observer"))
            observation_id = (row.get("inat_observation_id") or "").strip()
            county_label = (
                f"{(row.get('county_name') or '').strip()}|{(row.get('state_code') or '').strip()}"
            )
            obs_url = (row.get("inat_url") or "").strip()

            bucket = stats[observer]
            bucket["issue_rows"] = int(bucket["issue_rows"]) + 1
            if observation_id:
                bucket["observations"].add(observation_id)
            if county_label.strip("|"):
                bucket["counties"].add(county_label)
            if issue_type:
                bucket["issue_types"].add(issue_type)
            if obs_url:
                urls = bucket["sample_urls"]
                if len(urls) < max_samples and obs_url not in urls:
                    urls.append(obs_url)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "observer",
        "issue_rows",
        "unique_observations",
        "unique_counties",
        "issue_types",
        "sample_observation_urls",
    ]

    rows = []
    for observer, payload in stats.items():
        issue_rows = int(payload["issue_rows"])
        unique_observations = len(payload["observations"])
        unique_counties = len(payload["counties"])
        issue_types = sorted(payload["issue_types"])
        sample_urls = payload["sample_urls"]
        rows.append(
            {
                "observer": observer,
                "issue_rows": issue_rows,
                "unique_observations": unique_observations,
                "unique_counties": unique_counties,
                "issue_types": ";".join(issue_types),
                "sample_observation_urls": " | ".join(_host_path(url) for url in sample_urls),
            }
        )

    rows.sort(key=lambda r: (-int(r["issue_rows"]), str(r["observer"]).lower()))

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} users to {output_path}")
    if rows:
        print("Top users:")
        for row in rows[:10]:
            print(
                f"- {row['observer']}: {row['issue_rows']} issue rows, "
                f"{row['unique_observations']} observations, {row['unique_counties']} counties"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
