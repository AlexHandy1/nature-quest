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


def compute_top_n_specieskeys(con, fact_path: str, coverage: float = 0.99) -> list[str]:
    """Smallest set of specieskeys (by total occurrence_count summed across
    cells) accounting for at least `coverage` of all recorded occurrences -
    see prototypes/scripts/species_common_names_batch.py for the original
    exploration of why top-N-by-volume is the right prioritization. Runs
    entirely as a DuckDB aggregate query against fact_path (local or
    gs://) - the ~221M-row fact table never passes through Python, only
    this small result (specieskeys only) does."""
    rows = con.execute(f"""
        WITH species_totals AS (
            SELECT specieskey, SUM(occurrence_count) AS total_occ
            FROM read_parquet('{fact_path}')
            GROUP BY specieskey
        ),
        ranked AS (
            SELECT specieskey, total_occ,
                   SUM(total_occ) OVER (ORDER BY total_occ DESC ROWS UNBOUNDED PRECEDING) AS cum_occ
            FROM species_totals
        ),
        grand_total AS (SELECT SUM(total_occ) AS t FROM species_totals)
        SELECT specieskey
        FROM ranked, grand_total
        WHERE cum_occ - total_occ < {coverage} * t
        ORDER BY cum_occ
    """).fetchall()
    return [row[0] for row in rows]
