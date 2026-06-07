# `src/retrieval/` — Retrieval & Grounding (Section D · Fardin)

The knowledge read path + the grounding guarantee (HLD
[03](../../docs/clutch/hld/03-retrieval-grounding.md)). Company-scoped Moss query + grounded, cited
compose that refuses rather than inventing.

- **Exposes:** `load_index(company_id)`, `retrieve_for(RetrievalQuery) -> Chunk[]`,
  `compose(query, chunks) -> Answer`
- **Receives (passed in):** `gateway_client` (for `reason.compose`)
- **Imports only:** `src/contracts.py` — no sibling subfolder
- **Build-against mock:** `local_cosine` (Moss-free retrieve) + `template_compose`
- **Note:** can develop on the real `clutch-demo` Moss index immediately

**Run & API keys:** `python -m scripts.retrieval_mini --check` (mock — no keys). For real runs set
`MOSS_PROJECT_ID`/`MOSS_PROJECT_KEY` (`--live`) and `TRUEFOUNDRY_*` (`--real-compose`). Full setup:
[LLD README §2.1](../../docs/clutch/lld/README.md#21-retrieval--grounding-srcretrieval--lld-03) ·
spec: [LLD 03](../../docs/clutch/lld/03-retrieval-grounding.md).

See the brief: [14 — Fardin](../../docs/clutch/hld/engineers/14-fardin-inference-retrieval-voice.md)
and the connection plan [10 §5](../../docs/clutch/hld/engineers/10-work-division-and-fusing.md).
