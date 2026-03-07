from app.main import normalize_index_sort


def test_normalize_index_sort_defaults_to_title_asc():
    assert normalize_index_sort("") == "title_asc"
    assert normalize_index_sort("unknown") == "title_asc"


def test_normalize_index_sort_accepts_known_values():
    assert normalize_index_sort("title_asc") == "title_asc"
    assert normalize_index_sort("created_desc") == "created_desc"
