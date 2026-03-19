import sys
import types


psycopg2_module = types.ModuleType("psycopg2")
psycopg2_pool_module = types.ModuleType("psycopg2.pool")
psycopg2_pool_module.SimpleConnectionPool = object
psycopg2_extras_module = types.ModuleType("psycopg2.extras")
psycopg2_extras_module.RealDictCursor = object
psycopg2_module.pool = psycopg2_pool_module

sys.modules.setdefault("psycopg2", psycopg2_module)
sys.modules.setdefault("psycopg2.pool", psycopg2_pool_module)
sys.modules.setdefault("psycopg2.extras", psycopg2_extras_module)

from services.content_service import ContentService


class FakeQuery:
    def __init__(self):
        self.or_filters = []
        self.eq_filters = []

    def select(self, *args, **kwargs):
        return self

    def or_(self, expression: str):
        self.or_filters.append(expression)
        return self

    def eq(self, key: str, value: str):
        self.eq_filters.append((key, value))
        return self


class FakeSupabase:
    def __init__(self):
        self.query = FakeQuery()

    def table(self, _table: str):
        return self.query


def test_build_base_query_uses_normalized_columns_for_group_and_search():
    service = ContentService(FakeSupabase())

    query = service._build_base_query(
        table="channels",
        group="Noticias",
        country="ES",
        search="Canal Demo",
        include_count=True,
    )

    assert ("country", "ES") in query.eq_filters
    assert any("grupo_normalizado.ilike.%Noticias%" in expr for expr in query.or_filters)
    assert any("grupo.ilike.%Noticias%" in expr for expr in query.or_filters)
    assert any("nombre_normalizado.ilike.%Canal Demo%" in expr for expr in query.or_filters)
    assert any("nombre.ilike.%Canal Demo%" in expr for expr in query.or_filters)


def test_android_catalog_item_prefers_normalized_fields_for_display():
    service = ContentService(FakeSupabase())

    row = {
        "numero": 7,
        "nombre": "ES - Canal Demo HD",
        "nombre_normalizado": "Canal Demo HD",
        "grupo": "ES - Noticias",
        "grupo_normalizado": "Noticias",
        "logo": "https://img.test/channel.png",
        "url": "https://provider.test/live/user/pass/7.ts",
        "country": "ES",
    }

    item = service._to_android_catalog_item(row, "channels")

    assert item["title"] == "Canal Demo HD"
    assert item["subtitle"] == "Noticias"
    assert item["group"] == "Noticias"
    assert item["original_title"] == "ES - Canal Demo HD"
    assert item["original_group"] == "ES - Noticias"
