from iptv_api.core.catalog_visibility import is_allowed_catalog_item


def test_catalog_accepts_iptv_spanish_or_english():
    assert is_allowed_catalog_item({"has_iptv_source": True, "countries": ["ES"]})
    assert is_allowed_catalog_item({"has_iptv_source": True, "countries": ["EN"]})


def test_catalog_accepts_japanese_iptv_for_japanese_tmdb_title():
    assert is_allowed_catalog_item(
        {"has_iptv_source": True, "countries": ["JP"], "original_language": "ja"}
    )


def test_catalog_rejects_latino_only_and_unrelated_languages():
    assert not is_allowed_catalog_item({"has_iptv_source": True, "countries": ["LATAM"]})
    assert not is_allowed_catalog_item({"has_iptv_source": True, "countries": ["FR"]})


def test_catalog_accepts_marked_torrent_source():
    assert is_allowed_catalog_item({"has_torrent_source": True})
