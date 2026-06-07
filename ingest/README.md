# Fixalong ingestion — manual → signed Moss index

Parses repair manuals with **Unsiloed**, chunks them with heading-aware (repair-graph)
context, optionally **FACT-signs** each chunk for provenance, and injects them into the
**Moss** index the Fixalong live loop queries for verified repair context. This is the
*write side* of [HLD 11 (FACT × Moss)](../docs/fixalong/11-fact-moss-knowledge-trust.md).

```
PDF/DOCX/PPTX/URL ──(Unsiloed parse+poll)──▶ segments
                  ──(chunker: heading context + size merge + string metadata)──▶ chunks
                  ──(Moss create_index / add_docs upsert, batched)──▶ MOSS_INDEX_NAME
```

## Setup
```bash
cd ingest
uv sync                      # installs deps (incl. local ../fact) into .venv
cp .env.example .env.local   # fill in keys (use the SAME Moss values as the agent)
```

## Use
```bash
# Single file
python ingest.py docs/handbook.pdf

# Multiple files / a whole folder / a URL
python ingest.py docs/                       # every supported file in docs/
python ingest.py a.pdf b.docx c.pptx
python ingest.py https://example.com/spec.pdf

# Rebuild the index from scratch
python ingest.py docs/ --recreate

# Inspect chunking without touching Moss (no Moss creds needed)
python ingest.py docs/handbook.pdf --dry-run

# Save raw Unsiloed JSON to ./debug for tuning the chunker
python ingest.py docs/handbook.pdf --save-debug --dry-run
```

## How it behaves
- **Auto create-or-upsert:** creates `MOSS_INDEX_NAME` if missing, otherwise upserts
  (so you can add more docs to a live index incrementally).
- **Chunking** follows Moss's guidance: each chunk prepends its nearest heading so it's
  self-contained; small adjacent segments under the same section/page are merged toward
  ~900 chars (max 1500); all metadata values are strings.
- **Metadata** attached per chunk: `source`, `section`, `page`, `segment_count`,
  `doc_index` — usable later for Moss metadata filtering / citations in the UI.
- **Batched** uploads (500 docs/batch) to stay well within limits.

## Files
| File | Role |
|---|---|
| `unsiloed_client.py` | Submit parse job, poll until `Succeeded`, return result |
| `chunker.py` | Pure segment→chunk logic (testable, no I/O) |
| `ingest.py` | CLI: resolve sources → parse → chunk → inject into Moss |

## Verifiable retrieval (FACT)

Add `--sign` to issue a cryptographic provenance envelope per chunk, so the voice
agent can verify each fact offline before speaking it (act / hedge / escalate / refuse):

```bash
uv run python ingest.py docs/ --sign --fresh-days 30
```

See **[FACT_INTEGRATION.md](FACT_INTEGRATION.md)** for the full design — signing,
verifying, the metadata schema, the security model, and key management.

## Tuning the chunker
Edit `TARGET_CHARS` / `MAX_CHARS` in `chunker.py`. For real-time copilots, smaller, focused
chunks (600–900 chars) retrieve better and produce tighter on-screen context cards.
Use `--dry-run --save-debug` to eyeball Unsiloed's segment types and adjust
`HEADING_TYPES` / `SKIP_TYPES` for your document style.
```
