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


def compute_top_n_specieskeys(fact_rows: list[dict], coverage: float = 0.99) -> list[str]:
    """Smallest set of specieskeys (by total occurrence_count summed across
    cells) accounting for at least `coverage` of all recorded occurrences -
    see prototypes/scripts/species_common_names_batch.py for the original
    exploration of why top-N-by-volume is the right prioritization."""
    totals: dict[str, int] = {}
    for row in fact_rows:
        totals[row["specieskey"]] = totals.get(row["specieskey"], 0) + row["occurrence_count"]

    grand_total = sum(totals.values())
    target = coverage * grand_total

    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    top_n = []
    cumulative = 0
    for specieskey, total in ranked:
        top_n.append(specieskey)
        cumulative += total
        if cumulative >= target:
            break
    return top_n
