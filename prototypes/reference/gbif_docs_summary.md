# GBIF API reference — curated for the intent-query agent

This is a hand-curated summary of the GBIF API, scoped to exactly what this
feature needs: turning a natural-language walk request into a valid GBIF
species query. It is not a full copy of GBIF's documentation. All facts
below were verified live against `https://api.gbif.org/v1/` — see
`planning_and_status_docs/PLANNING_INTENT_QUERY_210726.md` for the full
verification trail if anything here looks wrong.

---

## 1. The two endpoints in play

1. **`GET /occurrence/search`** — returns real sighting records (with
   coordinates) for a given filter. This is what ultimately produces the
   species list. It does **not** accept species/taxon names as strings —
   only numeric keys (see §2).
2. **`GET /species/match`** — resolves a name (e.g. `"Aves"`) to the numeric
   key `occurrence/search` requires, and tells you whether the name was
   actually recognised.

Your job is to produce a rank + name pair for each taxon the user's request
implies (e.g. `{"taxonRank": "class", "taxonValue": "Aves"}`) — never a
numeric key yourself. Numeric GBIF keys are looked up in code afterwards,
not something you should generate or guess.

---

## 2. `occurrence/search` — taxonomy filters are numeric only

| Param | Type | Meaning |
|---|---|---|
| `kingdomKey` | integer | Kingdom classification key |
| `phylumKey` | integer | Phylum classification key |
| `classKey` | integer | Class classification key |
| `orderKey` | integer | Order classification key |
| `familyKey` | integer | Family classification key |
| `genusKey` | integer | Genus classification key |

**There is no string filter like `kingdom=Plantae` or `class=Aves` on this
endpoint** — a string value there is silently ignored, not an error. This is
why every taxon filter you produce must include both a `taxonRank` (one of
`kingdom`, `phylum`, `class`, `order`, `family`, `genus`) and a `taxonValue`
(the scientific name) — the rank tells the code which `*Key` param to
eventually fill in, once the name is resolved.

Other relevant params:
- **`q`** — simple full-text search over species/common names. It is
  **not** a descriptive or attribute search: GBIF has no data for colour,
  size, "impressiveness," or similar qualities. Only use `q` for genuine
  name-like words (e.g. `"oak"`, `"beetle"`). If the user's request has no
  name-like word left over after taxon filters are extracted, leave `q`
  empty — never invent a value for a descriptive term.
- Multiple filters in one request are ANDed together (all must match).
  There is no way to OR across different taxonomic ranks in a single
  request — that's handled outside this reference doc, in the query
  execution logic, not something you need to reason about here.

---

## 3. `species/match` — resolving a name

Call shape: `GET /v1/species/match?name={name}&rank={RANK}`

`RANK` is uppercase: `KINGDOM`, `PHYLUM`, `CLASS`, `ORDER`, `FAMILY`,
`GENUS`.

Example response for `name=Aves&rank=CLASS`:

```json
{
  "usageKey": 212,
  "scientificName": "Aves",
  "rank": "CLASS",
  "status": "ACCEPTED",
  "confidence": 94,
  "matchType": "EXACT",
  "kingdom": "Animalia",
  "kingdomKey": 1,
  "classKey": 212,
  "class": "Aves"
}
```

- `matchType` is `EXACT`, `FUZZY`, or `NONE`. Only `EXACT` and high-confidence
  `FUZZY` matches are accepted (handled in code, not something you decide).
- On `NONE`, most fields are missing entirely.

You never call this endpoint yourself — it's called in code, after you
produce a `{taxonRank, taxonValue}` pair. Your only job is to pick a
scientific name that's actually likely to resolve. That's what the rest of
this document is for.

---

## 4. Kingdoms — the full set (9 values, use these exact names)

| Lay term | Scientific name |
|---|---|
| Animals | Animalia |
| Plants | Plantae |
| Fungi / mushrooms | Fungi |
| Bacteria | Bacteria |
| Archaea | Archaea |
| Algae, diatoms, some protists | Chromista |
| Amoebas, other protists | Protozoa |
| Viruses | Viruses |

There is no 10th kingdom. If a request doesn't clearly match one of these
eight lay terms, don't force a kingdom filter — see §7.

---

## 5. Common animal classes (within kingdom Animalia)

Most everyday requests about animals ("birds," "insects," "mammals") are
really asking for a **class**, not the kingdom `Animalia` itself. Use these
exact scientific names when the request matches:

| Lay term | Scientific name (use as `taxonValue`, rank `class`) |
|---|---|
| Birds | Aves |
| Insects | Insecta |
| Mammals | Mammalia |
| Amphibians (frogs, toads, salamanders) | Amphibia |
| Spiders, scorpions, mites (arachnids) | Arachnida |
| Snails, slugs (gastropods) | Gastropoda |

### Reptiles — four classes, not one

GBIF's backbone retired the old single class `Reptilia` in 2022 (it was
paraphyletic — didn't cleanly separate reptiles from birds). There is no
single "reptile" class anymore. Instead, a reptiles request must become
**four separate filter entries**, all at rank `class`:

```json
[
  {"taxonRank": "class", "taxonValue": "Crocodylia"},
  {"taxonRank": "class", "taxonValue": "Squamata"},
  {"taxonRank": "class", "taxonValue": "Testudines"},
  {"taxonRank": "class", "taxonValue": "Sphenodontia"}
]
```

This is exactly the same mechanism as any other mixed-taxa request (e.g.
"birds, plants and mammals") — treat "reptiles" as shorthand for all four
groups together, not as a single term to resolve.

### Fish — five orders, a coverage-based holding solution

The common sense of "fish" doesn't map to one single GBIF class the way
reptiles do — ray-finned fish (most fish species) aren't a class in the
current backbone at all, they're split across ~46 separate orders, plus a
handful of unrelated classes (sharks, lampreys, etc). Rather than try to
be taxonomically complete, a fish request should use the **5 orders that
together account for the largest share of real fish observations in
GBIF** (~67% of all fish-related occurrence records globally, verified by
ranking all 52 fish-related classes/orders by actual GBIF occurrence
count):

```json
[
  {"taxonRank": "order", "taxonValue": "Perciformes"},
  {"taxonRank": "order", "taxonValue": "Cypriniformes"},
  {"taxonRank": "order", "taxonValue": "Scorpaeniformes"},
  {"taxonRank": "order", "taxonValue": "Gadiformes"},
  {"taxonRank": "order", "taxonValue": "Clupeiformes"}
]
```

This is a practical majority-coverage approximation, not a complete
answer — treat "fish" the same way as any mixed-taxa request (5 parallel
order filters), same as reptiles above.

---

## 6. Worked examples — lay term → filter

Use these as a guide for how to translate a phrase into a `{taxonRank,
taxonValue}` pair. Where no clean example exists, the request should
produce an empty `taxonFilters` list rather than a guess.

| User says | Produce |
|---|---|
| "plants" / "flowers" / "trees" | `{kingdom, Plantae}` — there is no separate "tree" or "flower" rank; kingdom is the right level. |
| "animals" | `{kingdom, Animalia}` |
| "birds" | `{class, Aves}` |
| "insects" / "bugs" | `{class, Insecta}` |
| "mammals" | `{class, Mammalia}` |
| "fungi" / "mushrooms" | `{kingdom, Fungi}` |
| "frogs" / "amphibians" | `{class, Amphibia}` |
| "spiders" | `{class, Arachnida}` |
| "snails" | `{class, Gastropoda}` |
| "reptiles" | four entries: `{class, Crocodylia}`, `{class, Squamata}`, `{class, Testudines}`, `{class, Sphenodontia}` |
| "fish" | five entries: `{order, Perciformes}`, `{order, Cypriniformes}`, `{order, Scorpaeniformes}`, `{order, Gadiformes}`, `{order, Clupeiformes}` — covers ~67% of real fish observations, not exhaustive. |
| "a mix of birds, plants and mammals" | three entries: `{class, Aves}`, `{kingdom, Plantae}`, `{class, Mammalia}` |
| "oaks" | `{kingdom, Plantae}` **and** `q: "oak"` — a genuine name-like word, safe to combine. |
| "something colourful" | **no taxon filter, no `q` value.** Colour isn't a GBIF field — don't invent a `q` term for it. |
| "something impressive / interesting" | **no taxon filter, no `q` value.** Not a real GBIF-queryable attribute. |
| "something rare" / "unusual" | no taxon filter (unless another part of the request implies one), `sort: rarest`. |
| "surprise me" / no clear signal | empty `taxonFilters`, `sort: most_observed` (the default). |

---

## 7. What NOT to do

- Never use unranked "classification browsing" labels as a `taxonValue` —
  things like `Vertebrata`, `Tetrapoda`, `Gnathostomata`, `Osteichthyes`,
  `Eukaryota`. These appear in GBIF's website when browsing a taxon's full
  lineage, but they are not reliable, resolvable names within the specific
  taxonomy this system queries. A live check of `Tetrapoda` (no rank
  given) actually resolved to an unrelated arachnid genus of the same
  name — a wrong-organism result, not a harmless miss. Stick to genuine
  kingdom/class-level names from §4/§5, or a specific order/family/genus
  name if the request is more precise than that.
- Never invent a numeric key. You only ever produce `taxonRank` +
  `taxonValue` (names); resolution to numbers happens elsewhere.
- Never put a qualitative/descriptive word (colour, size, "cute,"
  "impressive") into `q`. If that's all a request offers, produce no filter
  at all rather than guessing.
