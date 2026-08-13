"""Reglas SQL comunes para visibilidad del catalogo."""


def allowed_catalog_source_sql(catalog_alias: str, metadata_alias: str) -> str:
    """Devuelve la condicion de idioma/fuente permitida para un catalogo."""
    return f"""(
        {catalog_alias}.has_torrent_source = TRUE
        OR (
            {catalog_alias}.has_iptv_source = TRUE
            AND (
                {catalog_alias}.countries && ARRAY['ES', 'EN']::varchar[]
                OR (
                    {metadata_alias}.tmdb_data->>'original_language' = 'ja'
                    AND {catalog_alias}.countries && ARRAY['JP']::varchar[]
                )
            )
        )
    )"""


def is_allowed_catalog_item(item: dict) -> bool:
    """Evalua la regla para payloads JSON o respuestas de fallback."""
    if item.get("has_torrent_source"):
        return True
    if not item.get("has_iptv_source"):
        return False
    countries = set(item.get("countries") or [])
    if countries.intersection({"ES", "EN"}):
        return True
    return (item.get("original_language") or "").lower() == "ja" and "JP" in countries
