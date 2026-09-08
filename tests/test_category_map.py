from app.category_map import resolve_included_type, CATEGORY_MAP


def test_map_has_30_types():
    assert len(CATEGORY_MAP) >= 30


def test_common_resolutions():
    assert resolve_included_type("gimnasio") == "gym"
    assert resolve_included_type("gimnasios") == "gym"
    assert resolve_included_type("dentista") == "dentist"
    assert resolve_included_type("plomero") == "plumber"
    assert resolve_included_type("tecnologia") == "software_company"
    assert resolve_included_type("restaurante") == "restaurant"
    assert resolve_included_type("abogado") == "lawyer"


def test_case_and_space_insensitive():
    assert resolve_included_type("  Gimnasio ") == "gym"
    assert resolve_included_type("Peluqueria") == "hair_care"  # sin tilde


def test_no_match_returns_none():
    assert resolve_included_type("algo raro sin match") is None
    assert resolve_included_type("") is None
    assert resolve_included_type(None) is None