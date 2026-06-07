# Fixalong HLD 08 — Infrastructure & Enterprise Backbone (AWS)

**Status:** Draft v0.1 · **Build tier:** 2 · **Sponsor:** AWS / Bedrock
**Depends on:** — · **Consumed by:** Ingestion (01), Safety/routing (06), Voice (07)

---

## 1. Purpose

For the hackathon, AWS is optional plumbing. For the **enterprise story**, AWS is the
durable, governed knowledge backbone that makes Fixalong sellable to field-service orgs:

> For enterprise field repair, AWS becomes the private knowledge backbone — internal manuals,
> service logs, past tickets, images, and videos sit in a governed Bedrock/S3 environment,
> while **Moss handles the live local retrieval loop**.

Clean separation of concerns:
- **Bedrock / S3** = durable enterprise knowledge store (cold, governed, multimodal).
- **Unsiloed** = document structuring (01).
- **Moss** = real-time hot-path retrieval (02).
- **TrueFoundry** = gateway / guardrails / routing (06).
- **LiveKit** = the voice session (03).

## 2. What we use AWS for

**Hackathon (optional):**
- **Amazon Nova Sonic** as a speech-to-speech backend via the LiveKit AWS plugin (fallback
  voice path, 07).
- **Bedrock model fallback** behind the TrueFoundry gateway (06).
- **S3** for manual/asset storage.

**Enterprise (the narrative, mostly architecture not built today):**
- Customer **private manuals, service logs, past tickets** in a governed Bedrock/S3 env.
- **Bedrock Knowledge Bases** (incl. multimodal — images/audio/video) as the system of
  record; Moss session indexes are built *from* it for the live loop.
- Secure model access, audit, production deployment path.

## 3. Architecture (enterprise)

```
 enterprise sources (manuals, tickets, service logs, photos, videos)
        │
        ▼  S3  (durable, governed)
        │
        ▼  Bedrock Knowledge Base  (multimodal system of record)
        │        │
        │        ▼  Unsiloed structuring (01) on new docs
        ▼
   Moss session index  (built per repair, hot-path retrieval, 02)
        │
        ▼  live loop (03–07)
```

Hot/cold split: Bedrock/S3 is the **cold** system of record (everything, governed, durable);
Moss is the **hot** per-session retrieval layer (small, fast, ephemeral). This is the line
that makes the enterprise pitch credible without putting Bedrock on the <10 ms hot path.

## 4. Deployment (demo)
- Agent + orchestrator: containerized, run on a laptop or a single cloud instance.
- Moss: cloud index (or local) — no infra to tune.
- Secrets via env / local file (demo scope; see FACT key note in 06).

## 5. Interfaces
```python
store_manual(file) -> s3_uri                       # S3
kb_sync(s3_uri) -> kb_doc_id                        # Bedrock KB (enterprise)
nova_sonic_session(...) -> realtime_backend         # via LiveKit AWS plugin (07 fallback)
```

## 6. Failure modes & fallbacks
| Failure | Fallback |
|---|---|
| Nova Sonic unavailable | MiniMax / LiveKit default TTS (07) |
| Bedrock fallback model unreachable | TrueFoundry routes to another provider (06) |
| No AWS access during hackathon | skip entirely for the demo; keep it in the architecture/enterprise slide |

## 7. Open questions
- Is any AWS piece worth wiring live for the demo, or is it purely the enterprise-story slide?
  Default: enterprise-story only, unless Nova Sonic earns its place as the realtime backend.
- Multimodal Bedrock KB vs Moss for enterprise retrieval — position Moss as the *real-time*
  layer over Bedrock's durable store, not a competitor.
