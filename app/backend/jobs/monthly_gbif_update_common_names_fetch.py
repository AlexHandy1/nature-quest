import concurrent.futures
from collections.abc import Callable

DEFAULT_MAX_WORKERS = 20

# Pinned explicitly, not resolved live on every run. Why: our fact/
# dimension specieskeys come from whatever COL XR release GBIF had pinned
# at BigQuery's snapshot time; resolving this live at pipeline-run time
# could silently drift to a newer release than the one our occurrence data
# actually uses. Update this constant only via a deliberate decision - the
# same way the Testudines rank-override and the COL XR migration itself
# (WORK_SUMMARY_091026.md) were handled, never automatically.
#
# Verified against GBIF's registry 2026-10-23: resolves to ChecklistBank
# dataset 315557, alias "COL26.6 XR", version "2026-06-19 XR" - Catalogue
# of Life's Extended Release. To re-check: GET
# https://api.gbif.org/v1/dataset/7ddf754f-d193-4cc9-b351-99906754a03b and
# read the identifiers[].identifier where type is "CLB_DATASET_KEY", then
# GET https://api.checklistbank.org/dataset/{that key} and compare its
# alias/version against the ones recorded here.
CHECKLISTBANK_COL_XR_DATASET_KEY = 315557


def _is_code_like(name: str) -> bool:
    """Looks like a banding/abbreviation code (e.g. "COST", "GRTI"), not a name."""
    stripped = name.replace(" ", "").replace("-", "").replace(".", "")
    return stripped.isalpha() and stripped.isupper() and len(stripped) <= 5


def _is_informal_lowercase(name: str) -> bool:
    """Informal/slang entries in ChecklistBank's crowd-sourced data start
    lowercase (e.g. "pigeon", "kildeer"), unlike real English common names."""
    return name[0].islower()


def _is_non_ascii(name: str) -> bool:
    """Catches names in another script mistagged as English in the source
    data (e.g. an Indigenous-language name tagged language=eng)."""
    return not name.isascii()


def _is_junk(name: str) -> bool:
    return _is_code_like(name) or _is_informal_lowercase(name) or _is_non_ascii(name)


def pick_best_name(names: list[str]) -> str:
    candidates = [name for name in names if not _is_junk(name)]
    if not candidates:
        candidates = names
    return min(candidates, key=lambda name: (-len(name), name))


def build_common_name_map(records: list[dict]) -> dict[str, str]:
    names_by_taxon_id: dict[str, list[str]] = {}
    for record in records:
        names_by_taxon_id.setdefault(record["taxonID"], []).append(record["name"])
    return {taxon_id: pick_best_name(names) for taxon_id, names in names_by_taxon_id.items()}


def fetch_common_names_for_taxa(
    dataset_key: str,
    taxon_ids: list[str],
    fetch_fn: Callable[[str, str], list[dict]],
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> list[dict]:
    """Fans out one request per taxon, concurrently (bounded by max_workers).
    fetch_fn is injected so this is testable without a network call - real
    network reliability (retries, partial failures) is handled by the
    caller, since a failed fetch here is non-blocking for the pipeline
    (the existing common-names cache already provides durable coverage)."""
    records = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_fn, dataset_key, taxon_id): taxon_id for taxon_id in taxon_ids}
        for future in concurrent.futures.as_completed(futures):
            taxon_id = futures[future]
            for name_row in future.result():
                records.append({"taxonID": taxon_id, "name": name_row["name"]})
    return records
