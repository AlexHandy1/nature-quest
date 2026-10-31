# Semantic Species Search Experiment — Report (31 October 2026)

Track A's "cheapest first test" from `PLANNING_EMBEDDINGS_FEATURES_291026.md`, run
fully offline in `prototypes/`. **No production code (`app/`) was touched.** All work
is on branch `explore_wikipedia_species_embeddings`, not merged.

## TL;DR

- **Quality:** In 23 of 24 descriptive/thematic queries, at least one relevant species
  was in the top 5. Mean precision@5 was **0.60**. "I want to see pollinators" returned
  4 pollinators in its top 5 (bees, then the monarch). The model handles thematic
  queries like "animals that might live in a pond" and "something that lives in the
  ocean" well (P@5 = 1.0), which the current LLM→taxonomy resolver structurally can't
  answer.
- **Brief scenarios:** 3 of 4 passed.
  - **"something that flies" → birds: FAIL.** The top 5 are Spanish fly, fly agaric,
    housefly, emperor dragonfly and red admiral. The cause is a word match on "fly",
    not a lack of meaning: 4 of the 5 do fly. An ablation shows it isn't caused by
    putting names in the embedded text.
  - **"something that swims" → fish: weak pass.** Fish are at ranks 3–4, and a red
    admiral (a butterfly) is #1.
  - **"colourful wings… camouflage" → butterflies: pass.** Butterflies are at ranks 1–2.
- **Speed:** search is effectively instant once warm. The median query takes **~24ms**
  in-process (embedding ~15ms + DuckDB ~6ms) and **~32ms** over HTTP. Cold start is the
  real cost: **~9s**, almost all of it importing `torch`/`sentence-transformers`. That
  matters for Cloud Run.
- **Production batch estimate (~106k species):**
  - Wikipedia download takes **~6.4h sequential**, from a measured cold mean of
    218ms/request, or **~40min at 10× concurrency**.
  - Embedding takes **~64min on 8 CPU threads**, at 36ms/species batched.
  - Vectors need ~163MB stored.
  - The open unknown is the Wikipedia **hit rate** on the long tail, which this
    curated set cannot measure (see §5).
- **Confidence threshold, first signal:** out-of-domain queries scored a top-1 cosine
  of ≤0.225, against an in-domain median of 0.438. So a ~0.25–0.30 floor separates
  most cases, but vague queries ("something slimy", 0.179) fall below it. A floor like
  this works as a "send to the LLM fallback" trigger, not as a precise cutoff.

## 1. What was built

All files are new under `prototypes/`, following its conventions: standalone scripts,
full logging, light TDD for the deterministic logic only. The `venv/` is at the repo
root (gitignored).

| File | Purpose |
|---|---|
| `scripts/semantic_search_corpus.py` | Curated 159 global candidates (hand trait tags for judging only) → GBIF match + global occurrence count + Wikipedia REST summary + GBIF English vernacular names, **each request timed** → balanced 100-species selection |
| `scripts/semantic_search_index.py` | Embeds `"<common> (<scientific>): <extract>"` with `sentence-transformers/all-MiniLM-L6-v2` (384-dim, CPU) → DuckDB table `species` with a `FLOAT[384]` column; `search_store()` = `array_cosine_similarity` top-k |
| `scripts/server_semantic_search.py` + `web/index_semantic_search.html` | Prototype UI: Flask `POST /search` → top 5. Reskins the trail-guide tokens, nav, pill buttons and accordion species rows from `ux/species_detail_*_prototype.html`. The map, area control, LLM query and narration are all dropped |
| `scripts/semantic_search_experiment.py` | Latency (cold / warm in-process / HTTP) + 24 query scenarios + 3 out-of-domain controls, scored hit@5 and P@5 |
| `scripts/semantic_search_ablation.py` | Follow-up: does the embedded-text recipe cause the "fly" failure? |
| `scripts/semantic_search_scale_probe.py` | Synthetic-vector DuckDB search cost at 100 → 106k rows |
| `scripts/test_semantic_search.py` | 46 unit tests (group classification, tiering, common-name derivation, balanced selection, DuckDB round-trip, scoring) |
| `artifacts/semantic_search_corpus.json`, `semantic_search.duckdb` (780 KiB), `semantic_search_*_results.json`, `semantic_search_index_stats.json` | Run outputs |

To run the prototype:
`venv/bin/python prototypes/scripts/server_semantic_search.py`, then open http://127.0.0.1:5057.

### Corpus (100 species)

- **Taxon groups:** 18 — bird, mammal, fish, butterfly, moth, bee, ant/wasp, beetle,
  dragonfly, other insect, arachnid, reptile, amphibian, mollusc, crustacean, plant,
  fungus, other animal. Each has 4–6 species.
- **Prevalence tiers** come from global GBIF occurrence counts: common ≥1M, mid
  10k–1M, rare <10k. The split is **12 common / 65 mid / 23 rare**. Counts range from
  the house sparrow (26.3M records) down to the giant leaf insect (46) and Franklin's
  bumblebee (54).
- **Every species has a common name and a Wikipedia extract.**
  - 158/159 candidates qualified. The Lord Howe stick insect was excluded: its article
    title is the genus and it has no English GBIF vernacular name.
  - Common-name source: the Wikipedia article title when the scientific-name lookup
    redirected to a vernacular title, otherwise the most-cited English GBIF
    vernacular name.
- `specieskey` is GBIF's numeric usageKey, **not** the COL XR id used in production
  (the local Parquet index wasn't on this machine). A production build must re-key
  to the dimension table.

## 2. Experiment design

**Speed:**
- Cold start: package import, model load, DB open and first query.
- Warm in-process: 27 queries × 20 repeats, split into embed vs DuckDB search.
- HTTP round trip against the running server: 135 requests.

**Quality:** 24 scenarios in four groups:
- the brief's 4 scenarios;
- 4 paraphrases of them;
- 12 thematic queries;
- 4 lexical controls ("bees", "frogs and toads", "mushrooms", "flowers").

How relevance is judged:
- Relevance is decided **mechanically**: either by the species' GBIF-derived group
  (e.g. "fish") or by hand-assigned trait tags (`pollinator`, `flies`, `swims`,
  `freshwater`, `marine`, `nocturnal`, `venomous` = venomous/poisonous, `predator`,
  `raptor`, `tree`, `threatened`, `shell`).
- The tags are never embedded.
- **hit@5** (≥1 relevant species in the top 5) is the pass criterion the brief asked
  for. **P@5** is reported alongside it because hit@5 is lenient.

**Calibration:** 3 out-of-domain queries ("quarterly tax return spreadsheet", "best
pizza in Madrid", "how do I reset my router password"). Their top-1 cosine scores were
compared with the in-domain ones.

**Caveat:** I assigned the trait tags myself from general biology knowledge. They are
neither exhaustive nor independently reviewed. Treat P@5 as indicative, and look at
the per-query top-5 lists in `artifacts/semantic_search_experiment_results.json`.

## 3. Results

### 3.1 Speed (8-thread CPU container, no GPU)

| Stage | p50 | p95 | Notes |
|---|---|---|---|
| Query embedding | 15.2ms | 56.8ms | |
| DuckDB top-5 search (100 rows) | 5.9ms | 24.1ms | |
| **In-process total** | **23.9ms** | **66.7ms** | n=540 |
| Server-side total (Flask) | 26.8ms | 56.2ms | n=135 |
| **HTTP round trip** | **32.4ms** | **65.3ms** | n=135 |

**Cold start:**
- `import sentence_transformers`/`torch`: 7.8s
- model load (cached on disk): 1.1s (17.7s on first download from HuggingFace)
- DB open: 78ms
- first query: 152ms

The server takes 8.8s to be ready.

**Scale probe** (synthetic vectors, brute-force `array_cosine_similarity`):

| Rows | Unrestricted p50 | Restricted to 2k species p50 |
|---|---|---|
| 100 | 9.2ms | 4.9ms |
| 10,000 | 14.3ms | 13.4ms |
| 106,000 | 115.3ms | 13.8ms |

Restricting the search to species already in `candidate_cells`, as the planning doc
proposes, keeps search at ~14ms even with the full table loaded. Exhaustive search over
all 106k still finishes in ~115ms, so no ANN index is needed at this scale.

### 3.2 Query scenarios

Overall **hit@5 23/24, mean P@5 0.60**.

| Group | hit@5 | mean P@5 |
|---|---|---|
| Brief scenarios | 3/4 | 0.40 |
| Paraphrases | 4/4 | 0.80 |
| Thematic | 12/12 | 0.53 |
| Lexical controls | 4/4 | 0.80 |

| Query | Pass? | P@5 | Top 5 (✓ = relevant) |
|---|---|---|---|
| something that flies → birds | **FAIL** | 0.0 | Spanish fly, Fly agaric, Housefly, Emperor dragonfly, Red admiral |
| something that swims → fish | pass | 0.4 | Red admiral, Blue mussel, ✓Short-snouted seahorse, ✓Coelacanth, Common chameleon |
| something with colourful wings that use camouflage → butterflies | pass | 0.4 | ✓Red admiral, ✓Monarch, Asian hornet, Emperor dragonfly, Housefly |
| I want to see pollinators | pass | 0.8 | ✓Red mason bee, ✓Buff-tailed bumblebee, ✓Monarch, ✓Franklin's bumblebee, Leaf-cutter ant |
| flying animals | pass | 0.8 | ✓Housefly, Fly agaric, ✓Queen Alexandra's birdwing, ✓Spanish fly, ✓Emperor dragonfly |
| aquatic animals | pass | 1.0 | Coelacanth, seahorse, swamp lymnaea, brown trout, white-clawed crayfish |
| camouflaged butterflies | pass | 1.0 | 5 butterflies, incl. the dead-leaf butterfly at #4 |
| insects that visit flowers | pass | 0.4 | Giant leaf insect, Dandelion, ✓Bumblebee, ✓Birdwing, Glow-worm |
| animals that might live in a pond | pass | 1.0 | Swamp lymnaea, common frog, crayfish, carp, blue-tailed damselfly |
| creatures that come out at night | pass | 0.6 | ✓Glow-worm, Housefly, ✓Kākāpō, ✓Red fox, Swamp lymnaea |
| something venomous or poisonous | pass | 0.8 | ✓Golden poison frog, ✓Black widow, ✓Adder, ✓Death cap, Pill woodlouse |
| birds of prey | pass | 0.2 | ✓Golden eagle, Hummingbird hawk-moth, Blackbird, House sparrow, Red fox |
| tall trees | pass | 0.4 | ✓Wellingtonia, ✓Pedunculate oak, then 3 non-trees |
| endangered animals | pass | 0.4 | Red fox (#1, wrong), ✓crayfish, badger, ✓golden poison frog, hedgehog |
| something that lives in the ocean | pass | 1.0 | Blue mussel, coelacanth, green sea turtle, vaquita, octopus |
| animals with shells | pass | 0.4 | Hornet, oyster mushroom, toad, ✓blue mussel, ✓crayfish |
| a big predator | pass | 0.4 | ✓Red fox, Giant leaf insect, ✓Asian hornet, Axolotl, Red admiral |
| creepy crawlies with eight legs | pass | 0.4 | ✓Black widow, Pill woodlouse, ✓Wolf spider, Luna moth, Ladybird |
| something slimy | pass | 0.2 | All scores <0.18; only the blue mussel is relevant |
| colourful birds | pass | 0.6 | Golden eagle, blackbird, hawk-moth, house sparrow, red admiral |
| bees / frogs and toads / mushrooms / flowers | pass | 1.0 / 0.8 / 0.8 / 0.6 | Lexical controls behave as expected |

**Out-of-domain top-1 scores:** 0.135, 0.225, 0.110. In-domain top-1: min 0.179,
median 0.438, max 0.602.

**Prevalence bias:** the tier mix across all 120 returned results (16% common / 61% mid
/ 23% rare) closely matches the corpus (12 / 65 / 23). Semantic search surfaces rare
species as readily as common ones. That's good for discovery, but a real walk may
want results weighted by local occurrence.

### 3.3 Embedded-text ablation

| Recipe | hit@5 | mean P@5 |
|---|---|---|
| **name + extract (used)** | **23/24** | **0.60** |
| extract only | 22/24 | 0.60 |
| name only | 20/24 | 0.43 |

"something that flies" fails under **all three** recipes. The extracts themselves
contain "Spanish fly" and "fly agaric", so this is a polysemy weakness of a small
encoder. The name+extract recipe it was meant to test is not the cause. Names only
(no description) is clearly worse, which confirms the Wikipedia extract is doing the
work.

## 4. Interpretation

1. **The core hypothesis holds.** A pretrained encoder over Wikipedia extracts answers
   descriptive/thematic queries the current resolver can't ("pond", "ocean",
   "venomous", "pollinators" all reach P@5 ≥ 0.8) in ~25ms, with no LLM call.
2. **The failure modes are recognisable and specific:**
   - **Polysemy / surface-word pull:** "flies" pulls in fly-named species.
     **Implication for the planned cascade:** a naive lexical/`LIKE` first step would
     make this *worse*, because "flies" would substring-match "Spanish fly" or
     "Housefly". The lexical step should only accept whole-token matches against
     taxon names and group nouns ("bird", "beetle").
   - **Attribute/status queries:** for "endangered animals" the red fox ranks #1. The
     extracts rarely state conservation status prominently, and MiniLM latches onto
     "animals".
   - **Vague sensory queries:** "something slimy" scores are below the out-of-domain
     ceiling, i.e. effectively no signal. These should fall through to the LLM.
   - **Category vs member:** "something that swims" ranks a butterfly first. Short
     generic verbs produce low-contrast scores (0.25–0.28), so the ranking is close to
     noise.
3. **Confidence threshold:** a top-1 floor around 0.25–0.30 would reject all 3
   out-of-domain queries and the two weakest in-domain ones ("slimy" 0.179, "swims"
   0.281). That's roughly the right behaviour for a "send to LLM" trigger. It needs
   calibrating on a larger labelled set before being trusted.
4. **The model is the obvious lever.** all-MiniLM-L6-v2 is the smallest mainstream
   choice. The harness makes trying a retrieval-tuned model a one-constant change
   (`MODEL_NAME` + `DIM`) with directly comparable output, e.g. a `multi-qa-*` MiniLM
   or an e5/bge/gte "small" model.

## 5. Production batch estimate (~106k species) — estimate only, not run

All per-request numbers come from the **first (cold) corpus run**: 159 species,
sequential, one request at a time.

| Step | Measured | Extrapolated to 106k |
|---|---|---|
| Wikipedia REST summary | mean 218ms, p50 186ms, p95 423ms, max 4.1s | **~6.4h sequential**; ~40min at 10 concurrent workers, if latency-bound |
| MiniLM embedding (batch 32, 8 CPU threads) | 36ms/species (28/s) | **~64min**; ~2.8h if done one at a time (95ms) — batch it |
| DuckDB write | 658ms for 100 rows | negligible |
| Vector storage | 384 × float32 | **~163MB** raw, beside the dimension table |
| GBIF calls (match/count/vernacular) | 62 / 310 / 81ms | **not needed in production**: specieskey, counts and common names are already in the Parquet index (ADR-017) |

**Caveats on the estimate:**
- **Warm-cache distortion:** the second corpus run fetched the same 159 pages in a
  mean of **36ms** (CDN-warm). A real 106k run will be mostly cold-cache, so plan with
  ~200ms/request.
- **Hit rate is unknown:**
  - **Measured here:** 100% Wikipedia coverage, but these 159 candidates were picked
    *because* they are well known.
  - **Not measured:** the 106k-species long tail will have far lower English Wikipedia
    coverage. Misses also cost up to 2 requests if, like production's
    `wikipedia_client.py`, the lookup tries the common name first and then the
    scientific name.
  - **First thing to measure next:** sample ~500 random species from the real
    dimension table to get both the hit rate and the cold latency.
- **Rate limits:** the Wikimedia REST API expects an identifying User-Agent (set here)
  and polite concurrency. Check Wikimedia's current published limits before choosing a
  worker count. Wikimedia's bulk dumps would avoid per-request costs entirely and may
  be better for a full rebuild.
- **Embedding hardware:** the embedding figure is this container's CPU. A GPU, or more
  CPU cores on Cloud Run Jobs, would cut it a lot. Mean extract length was 98 tokens,
  and 1/100 exceeded MiniLM's 256-token limit (silently truncated).
- **Monthly refresh:** this should only re-fetch and re-embed new or changed extracts,
  so it costs far less than the full build.
- **Serving cost:** `torch` + `sentence-transformers` add ~9s of cold start and a large
  image (the CPU wheel is several hundred MB). For Cloud Run, consider an ONNX-exported
  encoder or a hosted embeddings API for the single query-time embedding.

## 6. Issues encountered

1. **GBIF leaves `class` empty for ray-finned fish.** Salmo trutta, Gadus morhua and
   others return `class=None`, so 11 fish were first classified as "other animal".
   Fixed by treating Chordata with no class as fish (test added).
2. **Noisy common names.** The first-listed GBIF vernacular name was often odd
   ("Native Scots Pinewood", "Blowball"), and some Wikipedia titles are scientific
   names ("Atropa bella-donna", "Pulchriphyllium giganteum"). Fixed in two ways, both
   with tests:
   - pick the most-cited English vernacular name;
   - treat a title that shares the genus or epithet as scientific.
3. **Browser validation via `agent-browser` wasn't possible.** The container has no
   root access and Chrome's system libraries are missing. I extracted the ~50 needed
   Debian packages locally and all libraries then resolved, but Chrome still hung even
   on a `data:` URL.
   - **Fallback:** loaded the real page in Node + jsdom against the live server,
     submitted queries and checked the rendered DOM.
   - **Confirmed:** 5 rows render, with images, scores and timings; rows expand with
     correct `aria-expanded`; an `<img onerror>` query renders as inert text (all
     content is set via `textContent`); over-length queries get a 400.
   - **Not checked:** visual layout. **Open the page yourself for a visual check.**
4. **No `gh` CLI or GitHub token**, so the branch was pushed but the PR has to be
   opened from the link below.
5. Unauthenticated HuggingFace download warning: harmless, the model is cached after
   the first download.

## 7. Recommended next steps

1. **Measure on real data:** sample ~500 random specieskeys from the production
   dimension table. Measure the Wikipedia hit rate and cold latency, and re-run this
   query suite with the polygon restriction.
2. **Swap the encoder:** run the same harness with one or two retrieval-tuned small
   models and compare hit@5, P@5 and the "flies"/"endangered" cases directly.
3. **Grow the eval set** to ~50 queries, with relevance labels from someone other than
   the person who wrote the queries, then calibrate the confidence floor.
4. **Cascade design:** make the lexical step whole-token only (see §4.2), and consider
   adding conservation status from a structured source (e.g. IUCN via GBIF) rather than
   relying on extract text.
5. **Serving:** decide between a local ONNX encoder and a hosted embeddings API before
   any production work, given the cold-start numbers.

## PR

Branch `explore_wikipedia_species_embeddings` is pushed and **not merged** (merging to
main would trigger a production deploy). Open the PR here:
https://github.com/AlexHandy1/nature-quest/compare/main...explore_wikipedia_species_embeddings?expand=1
