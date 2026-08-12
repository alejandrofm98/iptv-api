from types import SimpleNamespace

import pytest

from iptv_api.repositories.content_repo import ContentRepository
from iptv_api.repositories.series_repo import SeriesRepository


@pytest.mark.parametrize("repository", [ContentRepository, SeriesRepository])
def test_flatten_row_uses_column_names_for_orm_entities(repository):
    entity = SimpleNamespace(
        __table__=SimpleNamespace(
            columns=[SimpleNamespace(name="id"), SimpleNamespace(name="title")]
        ),
        id="catalog-id",
        title="Catalog title",
    )

    instance = repository.__new__(repository)
    result = instance._flatten_row({"entity": entity})

    assert result["id"] == "catalog-id"
    assert result["title"] == "Catalog title"
