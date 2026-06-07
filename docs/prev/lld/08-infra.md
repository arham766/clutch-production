# Fixalong LLD 08 — Infrastructure / config

**Implements:** HLD 08 · **Module(s):** agent/config.py · **Build tier:** 0 (config) / 2 (AWS) · **Status:** Draft v0.1

> Conventions, shared contracts and tech stack: see [README](README.md). Restated by reference.

## 1. Responsibility
Centralize all process configuration and secrets behind a single `agent/config.py` that loads
a `.env` via `python-dotenv`, validates the credentials each subsystem needs, and exposes one
frozen `Config` dataclass to the rest of the runtime. It also pins the demo deployment topology
(single container/laptop) and the hot/cold storage split (Moss = hot, Bedrock/S3 = cold). AWS
pieces are **Tier 2 / enterprise-slide** and never block the demo. This module owns *no business
logic* — it only resolves env, decides what is degraded, and fails fast on missing core creds.

## 2. Files & public surface
| File | Exports |
|---|---|
| `agent/config.py` | `Config` (frozen dataclass), `load_config() -> Config`, `ConfigError`, `Subsystem` (Literal) |
| `agent/pyproject.toml` | **uv** package (`[project]` + `[dependency-groups]`); deps incl. local `../fact` via `[tool.uv.sources]`; run with `uv run`, test `uv run pytest` |
| `.env.example` | documented template of every var below (committed, no secrets) |

**Packaging (uv everywhere):** `agent/`, `ingest/`, `fact/` are each a `uv` package (pyproject +
uv.lock); bring up with `uv sync`, run with `uv run`. The only non-uv part is the Next.js
frontend (`ui/`, npm/pnpm). No pip / requirements.txt / poetry / conda anywhere.

`load_config()` is called **once** at process start (orchestrator, LLD 13) and the result is
threaded through; no module re-reads `os.environ`.

## 3. Dependencies
- **Libs:** `python-dotenv` (load `.env`), stdlib `os`, `dataclasses`, `pathlib`, `typing`.
- **Internal:** none (this is the root; everyone imports it). Consumed by 02 (Moss), 03/07
  (LiveKit/MiniMax), 06 (TrueFoundry/Claude/Bedrock), 04 (Qwen), 11 (FACT key), 13 (orchestrator).
- **Env vars:** see §7 table (the authoritative list).

## 4. Data structures
```python
# agent/config.py
Subsystem = Literal["moss","claude","unsiloed","truefoundry","minimax","livekit","qwen","fact",
                    "aws","bedrock","nova_sonic"]

@dataclass(frozen=True)
class Config:
    # --- core (required; missing => ConfigError) ---
    moss_api_key: str; moss_base_url: str; moss_workspace: str           # MOSS_*
    claude_api_key: str                                                   # ANTHROPIC_API_KEY|CLAUDE_API_KEY
    truefoundry_api_key: str; truefoundry_base_url: str                   # TRUEFOUNDRY_* (Claude gateway, 06)
    livekit_url: str; livekit_api_key: str; livekit_api_secret: str       # LIVEKIT_*
    # --- feature creds (missing => that subsystem degraded, not fatal) ---
    unsiloed_api_key: str | None                                         # ingestion (01) — not on live path
    minimax_api_key: str | None; minimax_group_id: str | None            # TTS (07); fallback = LiveKit default
    qwen_api_key: str | None; qwen_base_url: str | None                  # vision (04); fallback = no perception
    fact_kb_private_key: str | None                                      # FACT signing (11); fallback = read-only KB
    # --- Tier 2 / enterprise (AWS; always optional) ---
    aws_region: str | None; aws_s3_bucket: str | None                    # cold store (S3)
    bedrock_model_id: str | None                                         # fallback model behind TrueFoundry (06)
    nova_sonic_enabled: bool                                             # realtime backend via LiveKit AWS plugin (07)
    # --- derived ---
    degraded: tuple[Subsystem, ...]                                      # subsystems running without creds
    profile: Literal["demo","enterprise"]                               # "enterprise" if AWS cold store configured
```
`ConfigError(Exception)` carries the list of missing **core** vars for a single clear message.

## 5. Key functions
```python
def load_config(env_path: str | None = None) -> Config:
    load_dotenv(env_path or find_dotenv(), override=False)   # real env wins over .env
    def need(*names) -> str:                                  # first non-empty of aliases
        for n in names:
            if (v := os.environ.get(n, "").strip()): return v
        missing.append(names[0]); return ""
    def opt(*names) -> str | None: ...                        # like need() but no missing-append

    missing: list[str] = []
    moss_api_key = need("MOSS_API_KEY"); moss_base_url = need("MOSS_BASE_URL"); ...
    claude_api_key = need("ANTHROPIC_API_KEY", "CLAUDE_API_KEY")   # accept either name
    tf_key = need("TRUEFOUNDRY_API_KEY"); ...
    livekit_url = need("LIVEKIT_URL"); ...
    if missing:                                               # CORE missing => hard fail
        raise ConfigError(f"missing required env vars: {', '.join(sorted(set(missing)))}")

    unsiloed = opt("UNSILOED_API_KEY"); minimax = opt("MINIMAX_API_KEY"); ...
    fact_key = opt("FACT_KB_PRIVATE_KEY")
    aws_bucket = opt("AWS_S3_BUCKET"); bedrock = opt("BEDROCK_MODEL_ID")
    nova = os.environ.get("NOVA_SONIC_ENABLED", "false").lower() in ("1","true","yes")

    degraded = tuple(name for cred, name in [                 # feature creds → degraded list
        (minimax,"minimax"), (qwen,"qwen"), (fact_key,"fact"), (unsiloed,"unsiloed")
    ] if not cred)
    profile = "enterprise" if (aws_bucket or bedrock or nova) else "demo"
    return Config(..., degraded=degraded, profile=profile)
```
Validation rule: **core** creds (Moss, Claude-via-TrueFoundry, LiveKit) are mandatory because
nothing runs without retrieval + reasoning + transport. Everything else degrades gracefully.

## 6. Control flow / sequence
```
process start
  └─ orchestrator.main()                       (LLD 13)
       └─ cfg = load_config()
            ├─ python-dotenv loads .env into os.environ (override=False)
            ├─ resolve + validate CORE  ──fail──▶ ConfigError → exit(1), print missing list
            ├─ resolve FEATURE creds    → degraded[]
            ├─ resolve AWS (Tier 2)     → profile
            └─ return frozen Config
       └─ log: "profile=demo degraded=[qwen]"  (visible at startup)
       └─ build clients (Moss/LiveKit/TF/MiniMax/Qwen) from cfg, never os.environ
```

## 7. Config & tuning — env table
| Var | Subsystem / LLD | Tier | Required? | Missing → |
|---|---|---|---|---|
| `MOSS_API_KEY`,`MOSS_BASE_URL`,`MOSS_WORKSPACE` | Moss retrieval (02) | 0 | **yes** | `ConfigError` |
| `ANTHROPIC_API_KEY` *or* `CLAUDE_API_KEY` | Reasoning (06) | 0 | **yes** | `ConfigError` |
| `TRUEFOUNDRY_API_KEY`,`TRUEFOUNDRY_BASE_URL` | Gateway/guardrail (06) | 0 | **yes** | `ConfigError` |
| `LIVEKIT_URL`,`LIVEKIT_API_KEY`,`LIVEKIT_API_SECRET` | Voice transport (03) | 0 | **yes** | `ConfigError` |
| `UNSILOED_API_KEY` | Ingestion structuring (01) | 0 | no | onboarding/ingest disabled (offline path) |
| `MINIMAX_API_KEY`,`MINIMAX_GROUP_ID` | TTS persona (07) | 0 | no | LiveKit default TTS |
| `QWEN_API_KEY`,`QWEN_BASE_URL` | Vision (04) | 0 | no | perception off (voice-only) |
| `FACT_KB_PRIVATE_KEY` | FACT signing (11) | 0 | no | KB read-only; verify still works |
| `AWS_REGION`,`AWS_S3_BUCKET` | S3 cold store | **2** | no | no manual archival; → enterprise profile |
| `BEDROCK_MODEL_ID` | Bedrock fallback (06) | **2** | no | TF routes to another provider |
| `NOVA_SONIC_ENABLED` | Nova Sonic realtime (07) | **2** | no | MiniMax / default realtime path |

Defaults: `NOVA_SONIC_ENABLED=false`, `override=False` (real shell env beats `.env`).

**Hot/cold split (HLD 08 §3).** Config makes the split explicit: `moss_*` = the **hot**
per-session retrieval layer (always required, on the <10 ms path); `aws_s3_bucket` /
`bedrock_model_id` = the **cold** governed system of record (Tier 2, never on the hot path).
The `profile` field is the toggle the enterprise slide points at.

## 8. Error handling & fallbacks (fail-safe)
- **Missing core cred** → raise `ConfigError` at startup with the exact var names; process exits
  before any session opens. No half-configured runtime (fail fast, not at first request).
- **Missing feature cred** → recorded in `cfg.degraded`; the owning module checks its flag and
  runs its documented fallback (per §7). Consistent with README §Errors: degrade, never invent.
- **AWS pieces unreachable at runtime** (Tier 2) → handled by their owners, not here: Nova Sonic
  down → MiniMax/default (07); Bedrock down → TrueFoundry reroutes (06); S3 down → skip archival.
  Config only decides *configured?*, not *reachable?* — no network calls in `load_config()`.
- `.env` file absent is **not** an error (env may come from the container/shell); only missing
  *resolved values* matter.

## 9. Latency / perf notes
`load_config()` runs once at boot, pure-CPU, no I/O beyond reading `.env` (sub-ms). It is **off
the hot path** entirely. Clients built from `Config` are reused for the session lifetime; no
per-turn env reads. Cold AWS resources (S3/Bedrock) are deliberately kept off the live retrieval
loop — only Moss (hot) is touched per turn.

## 10. Deployment topology (demo)
Single process, single host — laptop or one cloud instance:
```
┌─ host (laptop / single instance) ───────────────────────────┐
│  python process: LiveKit agent + orchestrator (agent/)      │
│     reads .env via config.py                                 │
│  next dev server (ui/, LLD 09)  ── browser ── localhost      │
└──────────────┬───────────────────────────────┬─────────────┘
   Moss cloud (or local) = HOT          LiveKit cloud = transport
   [Tier 2] S3 + Bedrock (TrueFoundry) = COLD / enterprise only
```
Container is optional for the demo (`uv run` on the laptop is sufficient); the same image
deploys to one cloud instance unchanged. No multi-service infra to tune (HLD 08 §4).

## 11. Test plan
- **Unit `test_config.py`:** (a) full env set → `load_config()` returns `Config`, `degraded==()`,
  `profile=="demo"`; (b) drop `MOSS_API_KEY` → `ConfigError` naming `MOSS_API_KEY`; (c) `CLAUDE_API_KEY`
  alias satisfies the Claude requirement; (d) omit `MINIMAX_API_KEY` → `"minimax" in cfg.degraded`,
  no error; (e) set `AWS_S3_BUCKET` → `profile=="enterprise"`; (f) `NOVA_SONIC_ENABLED=true` parses bool;
  (g) shell env overrides `.env` value (`override=False`). Mock via `monkeypatch.setenv` + `tmp_path/.env`.
- **Integration (smoke):** orchestrator boot with a real `.env` logs `profile`/`degraded` and
  builds clients without touching the network. No live AWS test (Tier 2, slide-only).

## 12. Build checklist
1. **Tier 0:** write `Config` + `load_config()` with core validation + `ConfigError`; `.env.example`.
2. **Tier 0:** wire `degraded` + per-subsystem fallback flags; orchestrator (13) startup log.
3. **Tier 0:** `test_config.py` (cases a–g) green.
4. **Tier 2:** add AWS fields (`aws_*`, `bedrock_model_id`, `nova_sonic_enabled`) + `profile`;
   keep them optional and off the hot path. Document on the enterprise slide.
