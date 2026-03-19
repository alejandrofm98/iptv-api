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


class HomeSupabaseQuery:
    def __init__(self, rows):
        self.rows = rows
        self.selected_counts = []
        self.eq_filters = []

    def select(self, _fields='*', count=None):
        self.selected_counts.append(count)
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, value: int):
        self.rows = self.rows[:value]
        return self

    def eq(self, key: str, value: str):
        self.eq_filters.append((key, value))
        return self

    def execute(self):
        class Result:
            def __init__(self, data):
                self.data = data
                self.count = len(data)

        return Result(self.rows)


class HomeSupabase:
    def __init__(self):
        self.queries = {
            'channels': HomeSupabaseQuery([
                {'numero': 1, 'nombre': 'ES - Canal Uno', 'nombre_normalizado': 'Canal Uno', 'grupo': 'ES - Noticias', 'grupo_normalizado': 'Noticias', 'logo': '', 'url': 'http://provider/live/user/pass/1.ts'},
            ]),
            'movies': HomeSupabaseQuery([
                {'numero': 2, 'nombre': 'ES - Movie One', 'nombre_normalizado': 'Movie One', 'grupo': 'ES - Cine', 'grupo_normalizado': 'Cine', 'logo': '', 'url': 'http://provider/movie/user/pass/2.mp4'},
            ]),
            'series': HomeSupabaseQuery([
                {'numero': 3, 'nombre': 'ES - Serie One S01 E01', 'nombre_normalizado': 'Serie One S01 E01', 'grupo': 'ES - Drama', 'grupo_normalizado': 'Drama', 'logo': '', 'url': 'http://provider/series/user/pass/3.mp4', 'serie_name': 'Serie One', 'temporada': '01', 'episodio': '01'},
            ]),
        }

    def table(self, name: str):
        return self.queries[name]


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


def test_home_catalog_uses_lightweight_queries_without_exact_count():
    service = ContentService(HomeSupabase())
    service.get_content_count = lambda: {'channels': 1, 'movies': 1, 'series': 1, 'replays': 0}
    service.get_content_list = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('home should not call get_content_list'))

    payload = service.get_home_catalog(username='demo', page_size=12)

    assert payload['featured_channels'][0]['title'] == 'Canal Uno'
    assert payload['featured_movies'][0]['title'] == 'Movie One'
    assert payload['featured_series'][0]['title'] == 'Serie One S01 E01'
    assert service.supabase.queries['channels'].selected_counts == [None]
    assert service.supabase.queries['movies'].selected_counts == [None]
    assert service.supabase.queries['series'].selected_counts == [None]


def test_home_catalog_can_filter_by_country():
    service = ContentService(HomeSupabase())
    service.get_content_count = lambda: {'channels': 1, 'movies': 1, 'series': 1, 'replays': 0}

    service.get_home_catalog(username='demo', page_size=12, country='EN')

    assert ('country', 'EN') in service.supabase.queries['channels'].eq_filters
    assert ('country', 'EN') in service.supabase.queries['movies'].eq_filters
    assert ('country', 'EN') in service.supabase.queries['series'].eq_filters
