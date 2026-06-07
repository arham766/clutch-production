# Fixalong LLD 01 — Ingestion (manual → repair graph → chunks)
**Implements:** HLD 01 · **Module(s):** `ingest/repair_graph.py` (NEW), wires into `ingest/ingest.py` · **Build tier:** 0 · **Status:** Draft v0.1

## 1. Responsibility
Turn the structured output of `unsiloed_client.parse()` into an **executable repair graph**
(`RepairNode`s, see shared contracts) and then into fine-grained, retrieval-ready `Chunk`
dicts carrying repair-specific string metadata (`type`, `node_id`, `part`, `risk_level`,
`hazard`, `symptom`, `model`). This module owns *only* the NEW middle stage of the HLD 01
pipeline (between `chunker` and `fact_moss.sign_chunks`); it does **not** parse PDFs, sign,
or talk to Moss — those are the already-built `unsiloed_client`, `fact_moss`, and `ingest`
modules, which it composes with. Output chunk dicts are the exact `{id, text, metadata}`
shape `sign_chunks`/`inject_into_moss` already consume, so signing + indexing are unchanged.

## 2. Files & public surface
| File | Exports |
|---|---|
| `ingest/repair_graph.py` (NEW) | `build_repair_graph(result, *, model, source) -> RepairGraph`; `graph_to_chunks(graph, *, source, extra_metadata=None) -> list[dict]`; `RepairGraph` (dataclass); `save_graph(graph, path)` |
| `ingest/ingest.py` (edit) | add `--repair-graph` / `--graph-out PATH` flags routing `parse → build_repair_graph → graph_to_chunks` instead of the flat `chunk_result` path |

`RepairNode` is the shared contract (`agent/contracts.py`) — **referenced, not redefined**.
`graph_to_chunks` emits the shared `Chunk` shape as plain dicts (matching `chunker.py`'s
`{id, text, metadata}` convention) so the rest of the pipeline is untouched.

## 3. Dependencies
- **Internal:** `chunker._slug`, `chunker._segment_text`, `chunker._iter_segments`,
  `chunker.HEADING_TYPES`/`SKIP_TYPES` (reuse, do not duplicate); `unsiloed_client` (upstream);
  `fact_moss.sign_chunks` + `ingest.inject_into_moss` (downstream, unchanged).
- **Libs:** stdlib only (`re`, `dataclasses`, `json`). No new deps.
- **Env vars:** none new (inherits `UNSILOED_API_KEY`, `MOSS_*`, `FACT_KB_*` via `ingest.py`).
- **`model` arg:** the model slug string (e.g. `hp-laserjet-x`) used for `model` metadata +
  node id namespacing; supplied by CLI (`--model-slug`) or onboarding (LLD 10).

## 4. Data structures (beyond shared contracts)
```python
@dataclass
class RepairGraph:
    nodes: list[RepairNode]          # shared contract RepairNode
    model: str                       # model slug, e.g. "hp-laserjet-x"
    source: str                      # originating manual label
    def to_dict(self) -> dict: ...   # {"model","source","nodes":[...]} -> repair_graph.json
```
Internal only: `_Segment` view = the raw Unsiloed segment dict (typed access via helpers).
Hazard entries reuse the contract shape `{"kind": str, "text": str}` (e.g.
`{"kind":"hot_surface","text":"Fuser may be hot..."}`).

## 5. Key functions

### 5.1 `build_repair_graph(result, *, model, source) -> RepairGraph`
Heuristic pass over Unsiloed segments (HLD 01 §6). One linear scan; a heading or numbered
step opens a node, subsequent segments are classified and attached.
```
node = None; nodes = []
for seg in _iter_segments(result):
    stype, text, page = seg.segment_type, _segment_text(seg), str(seg.page_number)
    if stype in SKIP_TYPES or not text: continue
    kind = classify(stype, text)                      # see 5.2
    if kind == "heading" or is_numbered_step_start(text):
        node = _new_node(id=_node_id(model, text), source={"manual":source,"page":page,
                         "heading":text}); nodes.append(node)
        node.trigger_phrases += _phrases(text)
        continue
    if node is None:                                  # preamble before first heading
        node = _new_node(id=_node_id(model,"intro"), source={"manual":source,"page":page})
        nodes.append(node)
    if   kind == "warning":  node.warnings.append(text);  node.hazards.append(_hazard(text))
    elif kind == "step":     node.instructions.append(_strip_step_number(text))
    elif kind == "part":     node.parts += _extract_parts(text)
    elif kind == "table":    node.conditions += _error_code_rows(seg)   # error-code tables
    elif kind == "caption":  node.parts += _extract_parts(text)          # diagram labels
    else:                    node.instructions.append(text)             # default = guidance
# second pass: resolve cross-references "see <heading>" -> node.next[] / conditions[]
_link_cross_refs(nodes)
return RepairGraph(nodes=nodes, model=model, source=source)
```

### 5.2 Classification heuristics (`classify`, regex-driven, case-insensitive)
| kind | signal |
|---|---|
| `heading` | `stype in HEADING_TYPES` |
| `warning` | text starts with / contains `^(warning|caution|danger|note|important)\b` callout |
| `step` | `is_numbered_step_start`: `^\s*(\d+[.)]|step\s+\d+)\b` |
| `table` | `stype == "Table"` and header row matches `error|code|fault|symptom` |
| `caption` | `stype in {"Caption","Picture"}`-adjacent label text |
| `part` | text matches a part-name pattern (noun + `roller|panel|tray|cartridge|fuser|...`) or appears in an exploded-view label list |
| `guidance` | fallback |

Helpers: `_hazard(text)` maps keywords→`kind` (`hot|burn`→`hot_surface`,
`shock|electric`→`electrical`, `pinch|moving`→`pinch_point`, else `general`) and sets
`risk_level` = `high` for hot/electrical, else `medium`. `_extract_parts(text)` returns
canonical part names matched against `PART_LEXICON` (+ aliases, HLD 01 §9 synonym list).
`_link_cross_refs` regex `\bsee\b.*?([A-Z][\w \-]{3,})` → fuzzy-match heading → append target
node id to `next` (and the condition phrase to `conditions`).

### 5.3 `graph_to_chunks(graph, *, source, extra_metadata=None) -> list[dict]`
Explodes each node into **one chunk per atomic unit** (instruction/warning/part/condition)
so retrieval is fine-grained (HLD 01 §5.1). Text is self-contained: node heading prepended
(same pattern as `chunker.flush()`).
```
out = []; src = _slug(source)
for node in graph.nodes:
  head = node.source.get("heading","")
  for i, instr in enumerate(node.instructions):
      out.append(_chunk(src, node, "step",    instr,    i, head, graph, part=_part_of(instr, node)))
  for i, warn in enumerate(node.warnings):
      hz = node.hazards[i] if i < len(node.hazards) else _hazard(warn)
      out.append(_chunk(src, node, "warning", warn, i, head, graph,
                        risk_level=hz["risk_level"], hazard=hz["kind"]))
  for i, part in enumerate(dict.fromkeys(node.parts)):     # dedup, stable order
      out.append(_chunk(src, node, "part", f"{part} — see {head}", i, head, graph, part=part))
  for i, cond in enumerate(node.conditions):
      out.append(_chunk(src, node, "error", cond, i, head, graph, symptom=_symptom_of(cond)))
return out
```
`_chunk(...)` builds `id = f"{src}-{node.id}-{type}-{i:03d}"`, prepends `head` to text when
absent, and assembles metadata (5.4), coercing **every value to `str`** and dropping empties
(same discipline as `chunker.flush()`). `extra_metadata` is merged last (e.g. `doc_index`).

### 5.4 Per-chunk metadata keys (all string-valued — Moss requirement, HLD 01 §5.2)
`source`, `section` (= node heading), `page`, `node_id`, `type`
(`step|warning|part|error|diagram|branch`), `model`, and conditionally `part`,
`risk_level` (`high|medium|low`), `hazard` (`hot_surface|electrical|pinch_point|general`),
`symptom`. FACT keys (`fact_envelope` …) are added **later** by `fact_moss.sign_chunk`
(not here). These match the `Chunk` contract's typed fields (`type`, `part`, `risk_level`),
which the agent reads back at retrieval (LLD 02) and safety (LLD 06).

## 6. Control flow / sequence
```
ingest.py main(--repair-graph)
  parse_and_chunk_graph(unsiloed, source)
    └─ result = unsiloed.parse(source)            # built — async submit→poll
    └─ graph  = build_repair_graph(result, model=slug, source=label)   # §5.1
    └─ save_graph(graph, args.graph_out or repair_graph.json)          # HLD 01 §3 output (a)
    └─ chunks = graph_to_chunks(graph, source=label, extra_metadata={"doc_index":i})  # §5.3
  → (optional) chunks = fact_moss.sign_chunks(chunks, kb_key, ...)     # built — unchanged
  → asyncio.run(inject_into_moss(chunks, index_name=..., ...))         # built — unchanged
```
Non-`--repair-graph` runs keep the existing flat `chunk_result` path untouched (backward
compatible). The signer/indexer never know whether chunks came from `chunker` or this module.

## 7. Config & tuning
- `STEP_RE = r"^\s*(\d+[.)]|step\s+\d+)\b"`, `CALLOUT_RE = r"^\s*(warning|caution|danger|note|important)\b"`.
- `HIGH_RISK_HAZARDS = {"hot_surface","electrical"}` → `risk_level=high`.
- `PART_LEXICON` + `PART_ALIASES` (synonym map, e.g. `"black rubber wheel"→"pickup roller"`),
  hand-seeded for the printer-jam demo (HLD 01 §6 pragmatic stance / §9).
- `CROSSREF_FUZZ = 0.8` heading match ratio (`difflib.SequenceMatcher`).
- Chunk text reuses `chunker` size sense implicitly (one atomic unit ≪ `MAX_CHARS`).

## 8. Error handling & fallbacks (fail-safe — HLD 01 §8)
- **Empty/`{"chunks":[]}` parse result** → `build_repair_graph` returns `RepairGraph(nodes=[])`;
  `graph_to_chunks` returns `[]` (mirrors `chunk_result` on empty input). No crash.
- **No headings / no numbered steps detected** → all content lands in a single `intro` node;
  still produces usable guidance chunks (degraded, not empty).
- **Unsiloed slow/erroring at build time** → caught upstream in `ingest.py`; commit a
  pre-parsed JSON and a hand-authored `repair_graph.json`, load via `save_graph`'s inverse
  `load_graph(path) -> RepairGraph` so the offline path never blocks the demo (HLD 01 §8).
- **Malformed segment (missing `segment_type`/`content`)** → skipped via the existing
  `_segment_text` empty-guard; never raises.
- **Metadata coercion**: any non-str value is `str(...)`-coerced and `None`/empty dropped, so
  a malformed heuristic value can never violate Moss's string-only contract.
- This stage never invents repair content — it only reorganizes parsed text; unverifiable
  text still gets gated downstream by FACT (LLD 06).

## 9. Latency / perf notes
Pure CPU, single linear scan + one cross-ref pass, O(segments). Offline/pre-demo, not on the
live path — budget is irrelevant (sub-second for a typical manual). Cold cost is dominated by
the upstream Unsiloed parse (built), not this module.

## 10. Test plan (reuses `ingest/tests/` pytest setup + `conftest.py` fixtures)
New file `ingest/tests/test_repair_graph.py`, importing `repair_graph` like the existing
tests import `chunker`/`ingest`. Reuse the `sample_parse_result` fixture and add a
`printer_jam_parse_result` fixture (Title + numbered steps + a `Warning` callout + an
error-code `Table` + a diagram caption).
- `test_steps_become_instructions` — numbered steps under a heading → one node, N `step` chunks.
- `test_warning_block_sets_risk_and_hazard` — `Warning: fuser is hot` → `type=warning`,
  `risk_level=high`, `hazard=hot_surface`.
- `test_error_code_table_becomes_error_chunks` — table rows → `type=error` chunks with `symptom`.
- `test_part_extraction_and_aliases` — `"black rubber wheel"` maps to `part=pickup roller`.
- `test_cross_reference_links_next` — `"if jam persists, see Rear Panel"` → target node id in `next`.
- `test_metadata_values_are_all_strings` — mirror `test_chunker.py`: every metadata value `str`.
- `test_expected_metadata_keys` — `{source,section,page,node_id,type,model}` ⊆ keys.
- `test_ids_are_unique_and_stable` — re-run yields identical ids (deterministic, like chunker).
- `test_empty_result_yields_empty_graph` — `{"chunks":[]}` → `nodes==[]`, chunks `==[]`.
- `test_no_headings_falls_back_to_intro_node` — heading-less input → single `intro` node, ≥1 chunk.
- **Integration** (extend `test_pipeline.py`): mock Unsiloed via `responses` with
  `printer_jam_parse_result`, run `parse → build_repair_graph → graph_to_chunks →
  inject_into_moss(fake_moss)`; assert created docs carry `node_id`/`type` metadata and are
  `moss.DocumentInfo`. Optionally chain `fact_moss.sign_chunks` (reuse `test_fact_moss.py`
  patterns) and assert `fact_envelope` is added without disturbing repair metadata.
- Mocks: Unsiloed HTTP via `responses`; Moss via the `fake_moss` fixture. No network/keys.

## 11. Build checklist (Tier-0 first)
1. `ingest/repair_graph.py`: `RepairGraph` dataclass + `save_graph`/`load_graph`.
2. Classification helpers (`classify`, `is_numbered_step_start`, `_hazard`, `_extract_parts`)
   reusing `chunker` helpers; seed `STEP_RE`/`CALLOUT_RE`/`PART_LEXICON`.
3. `build_repair_graph` single-pass builder + `_link_cross_refs` second pass.
4. `graph_to_chunks` explode + `_chunk` metadata assembly (string coercion).
5. `test_repair_graph.py` unit tests (§10) + `printer_jam_parse_result` fixture in `conftest.py`.
6. Wire `--repair-graph`/`--graph-out`/`--model-slug` into `ingest.py main()`; keep flat path default.
7. Extend `test_pipeline.py` with the graph integration test through `fake_moss`.
8. Hand-author `manuals/printer_repair_graph.json` for the demo as the §8 fallback.
