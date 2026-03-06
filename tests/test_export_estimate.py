from dataclasses import replace

from app.exports import estimate as estimate_module


def test_eta_ranges_for_items_produces_ordered_durations(monkeypatch):
    cfg = replace(
        estimate_module.export_config,
        include_all_photos=True,
        max_photos_per_observation=3,
        download_chunk_size=4,
        request_interval_seconds=2.5,
        xs_cadence_minutes=5,
        s_cadence_minutes=10,
        m_cadence_minutes=20,
        l_cadence_minutes=60,
        l_window_start_hour=0,
        l_window_end_hour=6,
    )
    monkeypatch.setattr(estimate_module, "export_config", cfg)

    out = estimate_module.estimate_eta_ranges_for_items(1200, bucket="L", avg_bytes_per_item=600_000)
    assert out["items_per_run"] >= 1
    assert out["runs_per_day"] >= 1
    assert out["best_days"] <= out["likely_days"] <= out["worst_days"]


def test_precheck_estimate_counts_are_non_negative():
    out = estimate_module.estimate_precheck_from_observations(0)
    assert out["observation_count"] == 0
    assert out["candidate_items"] >= 0
    assert out["eligible_items"] >= 0
