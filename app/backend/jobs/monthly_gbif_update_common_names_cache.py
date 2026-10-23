def new_entrants(top_n_specieskeys: list[str], existing_cache: dict[str, str]) -> list[str]:
    return [key for key in top_n_specieskeys if key not in existing_cache]


def merge_into_cache(existing_cache: dict[str, str], newly_fetched: dict[str, str]) -> dict[str, str]:
    return {**newly_fetched, **existing_cache}


def bootstrap_cache_from_dimension(dimension_rows: list[dict]) -> dict[str, str]:
    return {
        row["specieskey"]: row["common_name"]
        for row in dimension_rows
        if row["common_name"] is not None
    }
