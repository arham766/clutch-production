# `ondevice/` — On-Device (On-Prem Edge) Retrieval

Reference implementation of [LLD 03-LOCAL](../docs/clutch/lld/03-LOCAL-on-device-retrieval.md): Clutch
retrieval running **entirely on a company-owned edge box**. The proprietary corpus and its embeddings
are built and held on the box and **never pushed to the cloud**; embedding + search run **in-process,
offline**. The only corpus-derived egress is the retrieved chunk *text* sent for compose — to a
**local LLM when configured/healthy, else the cloud gateway**, or to a deterministic offline composer
for **zero egress**.

> **Isolated by design.** This package imports nothing from `src/` and nothing in the main pipeline
> imports it. It satisfies the same `(retrieve_for, compose)` shape the Agent's S4 seam expects, so it
> can later drop into the main `make_retrieval` registry unchanged — but for now it stands alone and
> runs **keyless** (stdlib only on the default path).

## Run it

```bash
# e2e gate — the full LOCAL-specific scenario battery (no keys, no network)
python ondevice/scripts/ondevice_mini.py --check

# one retrieve+compose turn (dev loop)
python ondevice/scripts/ondevice_mini.py --query "my iphone screen is cracked"

# pure unit invariants
uv run python -m pytest ondevice/tests -q
```

`--check` exits non-zero on any failure, so it drops straight into CI as the on-device gate.

## Layout

| File | What |
|---|---|
| `ondevice/contracts.py` | Self-contained `Chunk` / `Answer` / `RetrievalQuery` — mirrors `src/contracts.py` for drop-in compatibility, imports nothing. |
| `ondevice/config.py` | `OnDeviceConfig` — role-named keys (`retrieval_mode`, `reason_endpoint`, `local_index_path`, …) from env. |
| `ondevice/embed.py` | On-device embedder + in-memory `Session`. Real Moss embedder when the SDK is present, else a dependency-free lexical-cosine stand-in. `Session.push_index` exists only to be spied — it's never called. |
| `ondevice/local_store.py` | `LocalIndexStore` — the durable disk artifact (`chunks.jsonl` + `catalog.json` + `manifest.json`, atomic writes), `build_session()` (boot), and incremental `upsert()`. |
| `ondevice/retriever.py` | `OnDeviceMossRetriever` — in-process, offline retrieve behind the `Retriever` contract; product→company-wide widening; client-side latency. |
| `ondevice/compose.py` | Local-LLM-first endpoint resolver + `template_compose` (zero egress) + cited LLM compose. |
| `ondevice/registry.py` | Config-keyed `RETRIEVERS` table — selection is data, not branch code. |
| `ondevice/__init__.py` | `make_retrieval(cfg, gateway_client=None, local_client=None) -> (retrieve_for, compose)`. |
| `scripts/ondevice_mini.py` | The e2e gate + dev `--query` loop with mini stand-ins (`MiniLLM`, fixture corpus). |
| `tests/test_ondevice_unit.py` | Three pure invariants: registry dispatch, endpoint resolution, no-push. |

## What the gate proves

- **Boot from disk** — durability comes from the disk artifact, not in-memory state (survives a restart).
- **No-push** — `push_index` is never called across onboard→retrieve→compose; the corpus/vectors stay on the box.
- **Egress boundary** — template compose = zero outbound; local-LLM compose sends **chunk text only** (no vectors, no full corpus).
- **Local-LLM-first** — `auto` uses the local LLM when healthy, falls back to the gateway when not, and to the offline composer when neither is configured.
- **Incremental upsert** — an unchanged doc re-embeds zero chunks (manifest hash diff).
- **Registry dispatch + fail-loud** — a bad `retrieval_mode`/`reason_endpoint` raises `ConfigError` at build, not a silent wrong path.
- **Grounding parity** — a grounded query returns cited chunks; an absent query refuses — identical to the cloud backend.

## How it would merge into the main pipeline

When promoted, the three shipped files (`retriever.py`, `local_store.py`, `registry.py`) move into
`src/retrieval/`, `contracts.py` is dropped in favour of `src/contracts.py`, and `src/app.py` selects
the backend per `Company.retrieval_mode`. No caller changes — that's the point of the seam.
