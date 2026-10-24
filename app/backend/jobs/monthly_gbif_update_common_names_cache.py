def new_entrants(top_n_specieskeys: list[str], existing_cache: dict[str, str]) -> list[str]:
    return [key for key in top_n_specieskeys if key not in existing_cache]


def merge_into_cache(existing_cache: dict[str, str], newly_fetched: dict[str, str]) -> dict[str, str]:
    return {**newly_fetched, **existing_cache}


def bootstrap_cache_from_production(con, dimension_path: str) -> dict[str, str]:
    """One-time initialization when no cache file exists yet: seeds the
    cache from whatever common_name values are already in the current
    production dimension file, instead of starting empty and refetching
    everything via ChecklistBank. Only the (specieskey, common_name) pair
    for non-null rows comes back to Python (column-projected, filtered at
    the source via DuckDB against dimension_path, local or gs://) - the
    full dimension table is never materialized."""
    rows = con.execute(f"""
        SELECT specieskey, common_name FROM read_parquet('{dimension_path}')
        WHERE common_name IS NOT NULL
    """).fetchall()
    return {specieskey: common_name for specieskey, common_name in rows}
