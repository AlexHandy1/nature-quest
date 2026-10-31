"""
PROTOTYPE — semantic_search_corpus.py (Track A, step 1 of 3)

Question this answers: can we cheaply assemble a ~100-species corpus — spread
across taxon groups and across global prevalence (very common → rare), every
species with a real common name and a Wikipedia summary — and how long does
the Wikipedia download step take per species, so the cost of doing it for the
full ~106k-species dimension table can be estimated? See
docs/status_docs/PLANNING_EMBEDDINGS_FEATURES_291026.md (Track A).

Pipeline, per hand-curated candidate (CANDIDATES below — scientific name +
hand-assigned trait tags used only to judge the experiment, never embedded):
  1. GBIF species/match            → usageKey + taxonomy (kingdom/phylum/class/order/family)
  2. GBIF occurrence/search limit=0 → global occurrence count → prevalence tier
  3. Wikipedia REST page/summary   → extract, article title, image
  4. GBIF species/{key}/vernacularNames → English common names (fallback only)
Each request is timed individually. Wikipedia timing is the number that
matters for production planning: production already has common names joined
(ADR-017), so only step 3 would need running at ~106k scale.

Eligibility: GBIF match at SPECIES rank, a non-disambiguation Wikipedia
extract, and a common name (the Wikipedia article title when it differs from
the scientific name, else the first English GBIF vernacular name).
Selection: round-robin across taxon groups, cycling prevalence tiers within
each group, until 100 are chosen (select_balanced).

Note: `specieskey` here is GBIF's numeric backbone usageKey, NOT the COL XR
id used as `specieskey` in the production dimension table (see
prototypes/README.md §12). The local Parquet index isn't on this machine, so
prevalence comes from live global GBIF counts instead.

Output: prototypes/artifacts/semantic_search_corpus.json
Run:   venv/bin/python prototypes/scripts/semantic_search_corpus.py | tee prototypes/logs/semantic_search_corpus_$(date +%Y%m%d_%H%M%S).log
"""

import json
import statistics
import time
from itertools import cycle
from pathlib import Path
from urllib.parse import quote

import requests

GBIF_API = "https://api.gbif.org/v1"
WIKIPEDIA_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary"
USER_AGENT = "nature-quest-prototype/0.1 (alex.handy.research@gmail.com)"
TIMEOUT = 30
TARGET_COUNT = 100
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "artifacts" / "semantic_search_corpus.json"

COMMON_THRESHOLD = 1_000_000
MID_THRESHOLD = 10_000

BUTTERFLY_FAMILIES = {
    "Papilionidae", "Pieridae", "Nymphalidae", "Lycaenidae", "Riodinidae", "Hesperiidae", "Hedylidae",
}
FISH_CLASSES = {"Actinopterygii", "Elasmobranchii", "Chondrichthyes", "Sarcopterygii", "Coelacanthi",
                "Holocephali", "Petromyzonti", "Myxini"}
REPTILE_CLASSES = {"Reptilia", "Squamata", "Testudines", "Crocodylia", "Sphenodontia"}
CRUSTACEAN_CLASSES = {"Malacostraca", "Branchiopoda"}
BEE_FAMILIES = {"Apidae", "Megachilidae", "Andrenidae", "Halictidae", "Colletidae"}
TIERS = ("common", "mid", "rare")

# Trait tags (hand-assigned, used only for experiment relevance judgement):
#   flies, swims, freshwater, marine, nocturnal, pollinator,
#   venomous (= venomous OR poisonous/toxic), predator, raptor, tree,
#   threatened (IUCN VU/EN/CR or equivalent), shell
CANDIDATES = [
    # Birds
    ("Passer domesticus", ["flies"]),
    ("Turdus merula", ["flies"]),
    ("Columba livia", ["flies"]),
    ("Pica pica", ["flies"]),
    ("Erithacus rubecula", ["flies"]),
    ("Cyanistes caeruleus", ["flies"]),
    ("Aquila chrysaetos", ["flies", "predator", "raptor"]),
    ("Falco peregrinus", ["flies", "predator", "raptor"]),
    ("Bubo bubo", ["flies", "predator", "raptor", "nocturnal"]),
    ("Tyto alba", ["flies", "predator", "raptor", "nocturnal"]),
    ("Anas platyrhynchos", ["flies", "swims", "freshwater"]),
    ("Alcedo atthis", ["flies", "freshwater", "predator"]),
    ("Strigops habroptilus", ["nocturnal", "threatened"]),
    ("Aptenodytes forsteri", ["swims", "marine"]),
    ("Calypte anna", ["flies", "pollinator"]),
    ("Gymnogyps californianus", ["flies", "threatened"]),
    ("Aquila adalberti", ["flies", "predator", "raptor", "threatened"]),
    # Mammals
    ("Vulpes vulpes", ["nocturnal", "predator"]),
    ("Erinaceus europaeus", ["nocturnal"]),
    ("Sciurus vulgaris", []),
    ("Capreolus capreolus", []),
    ("Meles meles", ["nocturnal"]),
    ("Pipistrellus pipistrellus", ["flies", "nocturnal"]),
    ("Lutra lutra", ["swims", "freshwater", "predator"]),
    ("Phocoena sinus", ["swims", "marine", "threatened"]),
    ("Lynx pardinus", ["predator", "threatened"]),
    ("Panthera tigris", ["predator", "threatened"]),
    ("Ailuropoda melanoleuca", ["threatened"]),
    ("Megaptera novaeangliae", ["swims", "marine"]),
    ("Leptonycteris yerbabuenae", ["flies", "nocturnal", "pollinator"]),
    ("Castor fiber", ["swims", "freshwater"]),
    # Fish
    ("Salmo trutta", ["swims", "freshwater"]),
    ("Cyprinus carpio", ["swims", "freshwater"]),
    ("Esox lucius", ["swims", "freshwater", "predator"]),
    ("Gadus morhua", ["swims", "marine"]),
    ("Carcharodon carcharias", ["swims", "marine", "predator"]),
    ("Hippocampus hippocampus", ["swims", "marine"]),
    ("Thunnus thynnus", ["swims", "marine", "predator"]),
    ("Salmo salar", ["swims", "freshwater", "marine"]),
    ("Latimeria chalumnae", ["swims", "marine", "threatened"]),
    ("Cyprinodon diabolis", ["swims", "freshwater", "threatened"]),
    ("Synanceia verrucosa", ["swims", "marine", "venomous"]),
    ("Gasterosteus aculeatus", ["swims", "freshwater"]),
    ("Mola mola", ["swims", "marine"]),
    # Butterflies
    ("Vanessa atalanta", ["flies", "pollinator"]),
    ("Aglais io", ["flies", "pollinator"]),
    ("Pieris rapae", ["flies", "pollinator"]),
    ("Danaus plexippus", ["flies", "pollinator", "venomous"]),
    ("Kallima inachus", ["flies", "pollinator"]),
    ("Polyommatus icarus", ["flies", "pollinator"]),
    ("Papilio machaon", ["flies", "pollinator"]),
    ("Gonepteryx rhamni", ["flies", "pollinator"]),
    ("Ornithoptera alexandrae", ["flies", "pollinator", "threatened"]),
    ("Iphiclides podalirius", ["flies", "pollinator"]),
    # Moths
    ("Biston betularia", ["flies", "nocturnal"]),
    ("Macroglossum stellatarum", ["flies", "pollinator"]),
    ("Actias luna", ["flies", "nocturnal"]),
    ("Saturnia pavonia", ["flies"]),
    ("Tyria jacobaeae", ["flies", "venomous"]),
    ("Acherontia atropos", ["flies", "nocturnal"]),
    # Bees
    ("Apis mellifera", ["flies", "pollinator", "venomous"]),
    ("Bombus terrestris", ["flies", "pollinator", "venomous"]),
    ("Bombus franklini", ["flies", "pollinator", "venomous", "threatened"]),
    ("Xylocopa violacea", ["flies", "pollinator"]),
    ("Osmia bicornis", ["flies", "pollinator"]),
    ("Andrena fulva", ["flies", "pollinator"]),
    # Ants and wasps
    ("Vespa crabro", ["flies", "venomous", "predator"]),
    ("Vespula vulgaris", ["flies", "venomous", "predator"]),
    ("Formica rufa", ["venomous"]),
    ("Atta cephalotes", []),
    ("Vespa velutina", ["flies", "venomous", "predator"]),
    # Beetles
    ("Coccinella septempunctata", ["flies", "predator"]),
    ("Lucanus cervus", ["flies"]),
    ("Lampyris noctiluca", ["nocturnal"]),
    ("Dytiscus marginalis", ["swims", "freshwater", "predator"]),
    ("Melolontha melolontha", ["flies", "nocturnal"]),
    ("Dynastes hercules", ["nocturnal"]),
    ("Rosalia alpina", ["flies", "threatened"]),
    ("Lytta vesicatoria", ["flies", "venomous"]),
    # Dragonflies and damselflies
    ("Anax imperator", ["flies", "freshwater", "predator"]),
    ("Calopteryx virgo", ["flies", "freshwater", "predator"]),
    ("Sympetrum striolatum", ["flies", "freshwater", "predator"]),
    ("Ischnura elegans", ["flies", "freshwater", "predator"]),
    # Other insects
    ("Gryllus campestris", []),
    ("Mantis religiosa", ["predator"]),
    ("Phyllium giganteum", []),
    ("Dryococelus australis", ["nocturnal", "threatened"]),
    ("Episyrphus balteatus", ["flies", "pollinator"]),
    ("Culex pipiens", ["flies"]),
    ("Musca domestica", ["flies"]),
    ("Locusta migratoria", ["flies"]),
    ("Bombylius major", ["flies", "pollinator"]),
    # Arachnids
    ("Araneus diadematus", ["predator"]),
    ("Latrodectus mactans", ["predator", "venomous"]),
    ("Lycosa tarantula", ["predator", "venomous", "nocturnal"]),
    ("Buthus occitanus", ["predator", "venomous", "nocturnal"]),
    ("Ixodes ricinus", []),
    ("Argiope bruennichi", ["predator"]),
    ("Pisaura mirabilis", ["predator"]),
    # Reptiles
    ("Podarcis muralis", []),
    ("Chelonia mydas", ["swims", "marine", "threatened", "shell"]),
    ("Vipera berus", ["venomous", "predator"]),
    ("Testudo hermanni", ["shell", "threatened"]),
    ("Varanus komodoensis", ["predator", "threatened", "venomous"]),
    ("Crocodylus niloticus", ["swims", "freshwater", "predator"]),
    ("Natrix natrix", ["swims", "freshwater", "predator"]),
    ("Chamaeleo chamaeleon", ["predator"]),
    ("Trachemys scripta", ["swims", "freshwater", "shell"]),
    ("Dermochelys coriacea", ["swims", "marine", "shell", "threatened"]),
    # Amphibians
    ("Bufo bufo", ["freshwater", "nocturnal", "venomous"]),
    ("Rana temporaria", ["swims", "freshwater"]),
    ("Salamandra salamandra", ["nocturnal", "venomous"]),
    ("Triturus cristatus", ["swims", "freshwater"]),
    ("Ambystoma mexicanum", ["swims", "freshwater", "threatened"]),
    ("Phyllobates terribilis", ["venomous", "threatened"]),
    ("Atelopus zeteki", ["venomous", "threatened"]),
    ("Pelophylax perezi", ["swims", "freshwater"]),
    ("Hyla arborea", ["nocturnal"]),
    # Molluscs
    ("Cornu aspersum", ["shell", "nocturnal"]),
    ("Lymnaea stagnalis", ["freshwater", "shell"]),
    ("Mytilus edulis", ["marine", "shell"]),
    ("Octopus vulgaris", ["swims", "marine", "predator"]),
    ("Arion ater", ["nocturnal"]),
    ("Conus geographus", ["marine", "shell", "venomous", "predator"]),
    ("Sepia officinalis", ["swims", "marine", "predator"]),
    ("Margaritifera margaritifera", ["freshwater", "shell", "threatened"]),
    # Crustaceans
    ("Carcinus maenas", ["marine", "shell"]),
    ("Homarus gammarus", ["marine", "shell"]),
    ("Armadillidium vulgare", ["nocturnal", "shell"]),
    ("Austropotamobius pallipes", ["freshwater", "shell", "threatened"]),
    ("Procambarus clarkii", ["freshwater", "shell"]),
    ("Triops cancriformis", ["swims", "freshwater"]),
    # Plants
    ("Quercus robur", ["tree"]),
    ("Bellis perennis", []),
    ("Taraxacum officinale", []),
    ("Sequoiadendron giganteum", ["tree", "threatened"]),
    ("Pinus sylvestris", ["tree"]),
    ("Lavandula angustifolia", []),
    ("Helianthus annuus", []),
    ("Rafflesia arnoldii", ["threatened"]),
    ("Wollemia nobilis", ["tree", "threatened"]),
    ("Nymphaea alba", ["freshwater"]),
    ("Drosera rotundifolia", ["predator"]),
    ("Ophrys apifera", []),
    ("Atropa belladonna", ["venomous"]),
    ("Fagus sylvatica", ["tree"]),
    ("Ginkgo biloba", ["tree", "threatened"]),
    # Fungi
    ("Amanita muscaria", ["venomous"]),
    ("Boletus edulis", []),
    ("Cantharellus cibarius", []),
    ("Amanita phalloides", ["venomous"]),
    ("Pleurotus ostreatus", []),
    ("Laetiporus sulphureus", []),
    # Other animals
    ("Aurelia aurita", ["swims", "marine", "venomous"]),
    ("Lumbricus terrestris", ["nocturnal"]),
    ("Asterias rubens", ["marine", "predator"]),
    ("Actinia equina", ["marine", "venomous"]),
    ("Physalia physalis", ["marine", "venomous"]),
    ("Hirudo medicinalis", ["swims", "freshwater"]),
]


# ── Pure logic (unit tested in test_semantic_search.py) ────────────────

def classify_group(taxonomy: dict) -> str:
    """Maps GBIF higher taxonomy to a coarse, human-meaningful group label
    used for diversity balancing and for judging query relevance."""
    kingdom = taxonomy.get("kingdom")
    phylum = taxonomy.get("phylum")
    cls = taxonomy.get("class")
    order = taxonomy.get("order")
    family = taxonomy.get("family")

    if kingdom == "Plantae":
        return "plant"
    if kingdom == "Fungi":
        return "fungus"
    if cls == "Aves":
        return "bird"
    if cls == "Mammalia":
        return "mammal"
    if cls in FISH_CLASSES or (phylum == "Chordata" and cls is None):
        # GBIF's backbone leaves class empty for ray-finned fish (verified
        # live: Salmo trutta, Gadus morhua, Mola mola all return class=None).
        return "fish"
    if cls in REPTILE_CLASSES:
        return "reptile"
    if cls == "Amphibia":
        return "amphibian"
    if cls == "Arachnida":
        return "arachnid"
    if cls in CRUSTACEAN_CLASSES:
        return "crustacean"
    if phylum == "Mollusca":
        return "mollusc"
    if cls == "Insecta":
        if order == "Lepidoptera":
            return "butterfly" if family in BUTTERFLY_FAMILIES else "moth"
        if order == "Hymenoptera":
            return "bee" if family in BEE_FAMILIES else "ant_wasp"
        if order == "Coleoptera":
            return "beetle"
        if order == "Odonata":
            return "dragonfly"
        return "other_insect"
    return "other_animal"


def assign_tier(occurrence_count: int) -> str:
    if occurrence_count >= COMMON_THRESHOLD:
        return "common"
    if occurrence_count >= MID_THRESHOLD:
        return "mid"
    return "rare"


def derive_common_name(scientific_name: str, wikipedia_title: str | None,
                       gbif_english_names: list[str]) -> tuple[str | None, str | None]:
    """Wikipedia's article title is the cleanest common name when the
    scientific-name lookup redirected to a vernacular title; otherwise fall
    back to the English GBIF vernacular name cited by the most sources (the
    first-listed one is often an oddity, e.g. "Native Scots Pinewood")."""
    if wikipedia_title and not _looks_scientific(wikipedia_title, scientific_name):
        return wikipedia_title, "wikipedia_title"
    vernacular = _most_cited(gbif_english_names)
    if vernacular:
        return vernacular, "gbif_vernacular"
    return None, None


def _looks_scientific(title: str, scientific_name: str) -> bool:
    """A title sharing the genus or epithet is a scientific name (exact,
    re-spelt like "Atropa bella-donna", genus-only, or a renamed genus like
    "Pulchriphyllium giganteum"), not a common name."""
    genus, _, epithet = scientific_name.strip().lower().partition(" ")
    words = title.strip().lower().split()
    return words[0] == genus or words[-1] == epithet


def _most_cited(names: list[str]) -> str | None:
    counts: dict[str, int] = {}
    first_seen: dict[str, str] = {}
    for raw in names:
        for part in raw.split(","):
            name = part.strip()
            if not name:
                continue
            key = name.lower()
            counts[key] = counts.get(key, 0) + 1
            first_seen.setdefault(key, name)
    if not counts:
        return None
    best = max(counts, key=lambda k: counts[k])  # max() keeps the first-seen key on ties
    name = first_seen[best]
    return name[0].upper() + name[1:]


def select_balanced(records: list[dict], n: int) -> list[dict]:
    """Round-robin across groups; within each group, cycle through prevalence
    tiers so a group contributes common, mid and rare species before
    contributing a second species of any one tier."""
    per_group: dict[str, list[dict]] = {}
    for record in records:
        per_group.setdefault(record["group"], []).append(record)

    queues = {group: _tier_interleaved(members) for group, members in per_group.items()}
    selected: list[dict] = []
    while len(selected) < n and any(queues.values()):
        for group in list(queues):
            if queues[group] and len(selected) < n:
                selected.append(queues[group].pop(0))
    return selected


def _tier_interleaved(members: list[dict]) -> list[dict]:
    by_tier: dict[str, list[dict]] = {}
    for member in members:
        by_tier.setdefault(member["tier"], []).append(member)
    ordered: list[dict] = []
    tiers = [t for t in TIERS if t in by_tier]
    while any(by_tier[t] for t in tiers):
        for tier in tiers:
            if by_tier[tier]:
                ordered.append(by_tier[tier].pop(0))
    return ordered


# ── Network (timed) ─────────────────────────────────────────────────────

SESSION = requests.Session()
SESSION.headers["User-Agent"] = USER_AGENT


def timed_get(url: str, **params) -> tuple[requests.Response | None, float]:
    start = time.perf_counter()
    try:
        response = SESSION.get(url, params=params or None, timeout=TIMEOUT)
    except requests.RequestException as exc:
        print(f"    ! request failed: {url} — {exc}")
        response = None
    return response, (time.perf_counter() - start) * 1000


def fetch_candidate(scientific_name: str, tags: list[str]) -> dict:
    record: dict = {"scientific_name": scientific_name, "tags": tags, "timings_ms": {}}

    resp, ms = timed_get(f"{GBIF_API}/species/match", name=scientific_name, strict="true")
    record["timings_ms"]["gbif_match"] = ms
    match = resp.json() if resp is not None and resp.ok else {}
    if match.get("rank") != "SPECIES" or "usageKey" not in match:
        record["excluded_reason"] = f"no SPECIES-rank GBIF match ({match.get('matchType')}, {match.get('rank')})"
        return record
    key = match.get("acceptedUsageKey") or match["usageKey"]
    record["specieskey"] = str(key)
    record["taxonomy"] = {k: match.get(k) for k in ("kingdom", "phylum", "class", "order", "family")}
    record["group"] = classify_group(record["taxonomy"])

    resp, ms = timed_get(f"{GBIF_API}/occurrence/search", taxonKey=key, limit=0)
    record["timings_ms"]["gbif_count"] = ms
    record["occurrence_count"] = resp.json().get("count", 0) if resp is not None and resp.ok else 0
    record["tier"] = assign_tier(record["occurrence_count"])

    resp, ms = timed_get(f"{WIKIPEDIA_SUMMARY_URL}/{quote(scientific_name.replace(' ', '_'), safe='')}")
    record["timings_ms"]["wikipedia_summary"] = ms
    summary = resp.json() if resp is not None and resp.ok else {}
    if summary.get("type") == "disambiguation" or not summary.get("extract"):
        record["excluded_reason"] = "no Wikipedia summary extract"
        return record
    record["extract"] = summary["extract"]
    record["wikipedia_title"] = summary.get("title")
    record["wiki_url"] = (summary.get("content_urls") or {}).get("desktop", {}).get("page")
    record["image_url"] = (summary.get("thumbnail") or {}).get("source")

    resp, ms = timed_get(f"{GBIF_API}/species/{key}/vernacularNames", limit=200)
    record["timings_ms"]["gbif_vernacular"] = ms
    names = resp.json().get("results", []) if resp is not None and resp.ok else []
    english = [n["vernacularName"] for n in names if n.get("language") == "eng" and n.get("vernacularName")]
    common, source = derive_common_name(scientific_name, record["wikipedia_title"], english)
    if not common:
        record["excluded_reason"] = "no common name"
        return record
    record["common_name"] = common
    record["common_name_source"] = source
    return record


def summarise_timings(values: list[float]) -> dict:
    values = sorted(values)
    return {
        "n": len(values),
        "total_ms": sum(values),
        "mean_ms": statistics.mean(values),
        "p50_ms": statistics.median(values),
        "p95_ms": values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))],
        "max_ms": values[-1],
    }


def main() -> None:
    print(f"Fetching {len(CANDIDATES)} candidates (sequential, one request at a time)\n")
    run_start = time.perf_counter()
    records = []
    for i, (name, tags) in enumerate(CANDIDATES, 1):
        record = fetch_candidate(name, tags)
        records.append(record)
        t = record["timings_ms"]
        status = record.get("excluded_reason") or f"OK → {record['common_name']} [{record['group']}, {record['tier']}, {record['occurrence_count']:,}]"
        print(f"[{i:3}/{len(CANDIDATES)}] {name:32} wiki={t.get('wikipedia_summary', 0):6.0f}ms  {status}")
    wall_ms = (time.perf_counter() - run_start) * 1000

    eligible = [r for r in records if "excluded_reason" not in r]
    selected = select_balanced(eligible, TARGET_COUNT)

    timing_summary = {
        stage: summarise_timings([r["timings_ms"][stage] for r in records if stage in r["timings_ms"]])
        for stage in ("gbif_match", "gbif_count", "wikipedia_summary", "gbif_vernacular")
    }
    timing_summary["wall_clock_ms"] = wall_ms

    print(f"\nEligible: {len(eligible)}/{len(records)}   Selected: {len(selected)}")
    for r in records:
        if "excluded_reason" in r:
            print(f"  excluded: {r['scientific_name']} — {r['excluded_reason']}")
    print("\nSelected by group:")
    groups: dict[str, list] = {}
    for r in selected:
        groups.setdefault(r["group"], []).append(r["tier"])
    for g, tiers in sorted(groups.items()):
        print(f"  {g:14} {len(tiers):3}  " + ", ".join(f"{t}={tiers.count(t)}" for t in TIERS))
    print("\nSelected by tier:", {t: sum(r["tier"] == t for r in selected) for t in TIERS})
    print("\nTiming summary (ms):")
    for stage, s in timing_summary.items():
        if isinstance(s, dict):
            print(f"  {stage:18} n={s['n']:3} mean={s['mean_ms']:7.1f} p50={s['p50_ms']:7.1f} p95={s['p95_ms']:7.1f} max={s['max_ms']:7.1f} total={s['total_ms']/1000:6.1f}s")
    print(f"  wall clock: {wall_ms/1000:.1f}s")

    OUTPUT_PATH.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "candidate_count": len(records),
        "eligible_count": len(eligible),
        "timing_summary": timing_summary,
        "selected": selected,
        "all_candidates": records,
    }, indent=2, ensure_ascii=False))
    print(f"\nWrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
