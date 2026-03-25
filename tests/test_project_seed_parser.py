from app.main import parse_project_seed_values


def test_parse_project_seed_values_accepts_id_slug_and_observation_url_query():
    values, error = parse_project_seed_values(
        "124358\nfungi-of-alabama-ams-fundis-local-project\nhttps://www.inaturalist.org/observations?project_id=132913"
    )
    assert error is None
    assert values == [
        "124358",
        "fungi-of-alabama-ams-fundis-local-project",
        "132913",
    ]


def test_parse_project_seed_values_accepts_project_hash_prefix():
    values, error = parse_project_seed_values("Project # 251751")
    assert error is None
    assert values == ["251751"]
