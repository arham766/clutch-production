# Clutch LLDs — Section D (Inference: Retrieval / Voice serving · Fardin)

Implementation-level designs for the three **Limbs** Section D owns. Each LLD specifies **one
`src/` subfolder in isolation** — it imports only the **Spine** (`src/contracts.py`, `src/events.py`)
and receives every neighbour as a **passed-in argument**, so it **builds, tests, and demos with no
other section present**. Read the matching HLD first; these docs are the build spec.

| LLD | Limb | Implements (HLD) | Standalone test |
|---|---|---|---|
| [03 — Retrieval & Grounding](03-retrieval-grounding.md) | `src/retrieval/` | [HLD 03](../hld/03-retrieval-grounding.md) | `scripts/retrieval_mini.py --check` |
| [05 — Realtime & Voice](05-realtime.md) | `src/realtime/` | [HLD 05](../hld/05-realtime-voice-widget.md) (server) | `scripts/realtime_mini.py --check` |
| [05 — Speech Layer](05-speech-layer.md) | `src/speech/` | [HLD 05](../hld/05-realtime-voice-widget.md) §4 | `scripts/speech_e2e.py --check` |

Owner brief: [14 — Fardin](../hld/engineers/14-fardin-inference-retrieval-voice.md) ·
Work division & seams: [10](../hld/engineers/10-work-division-and-fusing.md).

---

## 0. Build status & what's left (live-verified 2026-06-07)

All three Limbs are implemented and tested against the **real services**. Two are fully green
end-to-end; realtime's transport is verified and its one remaining seam is the **Agent/LLM brain,
which is Tonmoy's integration (S2)**.

| Limb | Status | Verified live | Remaining work |
|---|---|---|---|
| `src/retrieval/` | ✅ **green e2e** | real Moss retrieve (~4 ms) + real grounded/cited compose + refusal | wire compose through the **TrueFoundry gateway** instead of OpenRouter once a base URL exists; have onboarding stamp `product_id` into chunk metadata so the filter binds |
| `src/speech/` | ✅ **green e2e** | real Cartesia TTS→STT round-trip (full phrase recovered) | none (extensible to other vendors when needed) |
| `src/realtime/` | ⚠️ **transport verified; brain pending** | worker dispatch → agent joins room → `session.start` completes; `lk.chat` text reaches the agent; events/data channel wired | **Tonmoy: inject the Agent as the session LLM** (see below). Then `scripts/realtime_mini.py --check` runs the full 5-modality gate as written — **the test is intentionally left unchanged for Tonmoy to run after wiring the brain.** |

### What needs to be done

1. **Realtime ← Agent/LLM integration (Tonmoy, S2) — the one blocker.** livekit-agents **1.5.17**
   guards `if self.llm is None: raise "trying to generate reply without an LLM model"`
   (`voice/agent_activity.py:1157`) — it requires a **non-None `session.llm`** *before* the
   overridden `ClutchAgent.llm_node` is ever reached. Our layer transports correctly (the agent
   joins, `lk.chat`/STT input arrives, the See path publishes events) but **cannot generate a spoken
   reply until the brain is present**. Tonmoy must provide his Agent as a LiveKit `llm.LLM` (or wrap
   it) so `app.py` constructs `AgentSession(llm=<brain>, stt=…, tts=…, vad=…)`. Once set, the existing
   `realtime_mini.py --check` exercises TALK/TYPE/BARGE/SEE/STATE end-to-end. *Do not fake an LLM to
   force the gate green — the brain is real work owned by 04.*
2. **Retrieval gateway path.** Compose was verified through **OpenRouter directly** because no
   `TRUEFOUNDRY_BASE_URL` was supplied. When the TrueFoundry gateway endpoint is known, set it and
   route `reason.compose` through it (no code change in `retrieval/` — only the injected client).
3. **Moss index seeding.** The Moss project had **zero indexes** and there is no `UNSILOED_API_KEY`,
   so `clutch-demo` was seeded directly from the fixture corpus via `scripts/seed_clutch_demo.py`
   (re-run with `python -m scripts.seed_clutch_demo`). Real company corpora need the onboarding/ingest
   pipeline (Unsiloed → Moss) with a real key.
4. **Realtime deprecations (pre-v2.0, non-blocking).** On 1.5.17 these still work but warn:
   `min_endpointing_delay`/`max_endpointing_delay`/`allow_interruptions`/`min_interruption_duration`
   (→ `TurnHandlingOptions`) and `RoomInputOptions`/`RoomOutputOptions` (→ `RoomOptions`). Migrate
   before upgrading to livekit-agents 2.0.

---

## 1. The independence rule (why each LLD stands alone)

Every Limb obeys the same contract, so it can be **written and tested by one engineer with zero
dependency on the others' code** (10 §5/§6):

- **Imports only the Spine** — `src/contracts.py` (+ `src/events.py` for `realtime/`). **Never** a
  sibling subfolder (`grep` for `from agent`, `from perception`, `from retrieval`, `from speech`
  must come up empty across the Limb).
- **Neighbours arrive injected** — the gateway client, the Agent, `identify`, `stt`/`tts`,
  `scope_session` are **passed in** (via a factory or `run_session(...)` args), never constructed
  inside the Limb.
- **One standalone script is the test** — each Limb has a single runnable harness in `scripts/` that
  brings the whole layer up with **real-shaped mini stand-ins** for the neighbours and a `--check`
  gate that exits non-zero on failure. That `--check` *is* the acceptance gate.
- **Keyless = SKIP, never FAIL** — on a bare clone with no secrets, every `--check` either runs the
  mock path or prints `SKIP (no creds)` and **exits 0**, so CI and a fresh checkout never flake.

### 1.1 Independence & test matrix

| Limb | Imports | Exposes | Receives (injected) | Standalone test | Needs keys? |
|---|---|---|---|---|---|
| `src/retrieval/` | `contracts` | `load_index`, `retrieve`, `retrieve_for`, `compose`, `make_retrieval` | `gateway_client`, `cfg` | `scripts/retrieval_mini.py --check` | **No** (mock). Optional: Moss (`--live`), gateway (`--real-compose`) |
| `src/realtime/` | `contracts`, `events` | `run_session`, `build_worker` | `agent`, `identify`, `stt`, `tts`, `vad`, `scope_session`, `cfg` | `scripts/realtime_mini.py --check` | **Yes** to run real (LiveKit + Cartesia); keyless ⇒ SKIP |
| `src/speech/` | `contracts` | `get_stt`, `get_tts` | `cfg` | `scripts/speech_e2e.py --check` | Selection: **No**. Round-trip: Cartesia; keyless ⇒ SKIP |

---

## 2. How to add the API for each piece to work

This is the operator guide: **which service, which env vars, which dependency group, and the command
that proves it works.** Keys live in **environment variables** (read lazily inside each provider),
**never** in `src/config.py` — config names *roles* (`stt_provider`, `reason_model`), env holds
*vendor identity/secrets* (00 §7). Copy [`.env.example`](#5-envexample-copy-paste) to `.env.local`
and fill in only the keys for the pieces you want live.

> **Order of operations for any piece:** (1) install its dependency group, (2) set its env vars in
> `.env.local`, (3) run its `--check`. With **no** keys set, every `--check` still passes (mock/SKIP).

### 2.1 Retrieval & Grounding (`src/retrieval/` — LLD 03)

Two independent edges: **Moss** (the index/read path) and the **gateway** (the compose LLM, injected).

| Service | Env var | Required for | Where to get it |
|---|---|---|---|
| **Moss** (retrieval index) | `MOSS_PROJECT_ID` | `--live` (real `retrieve`) | Moss portal → project id |
| | `MOSS_PROJECT_KEY` | `--live` | Moss portal → project key |
| **TrueFoundry gateway** (compose LLM) | `TRUEFOUNDRY_BASE_URL` | `--real-compose` with a real client | Gateway `/openai` endpoint (HLD 06) |
| | `TRUEFOUNDRY_API_KEY` | `--real-compose` with a real client | TrueFoundry portal |

- **Config knobs** (role-named, set in `src/config.py`, *not* env): `retrieval_alpha` (0.70),
  `retrieval_top_k` (5), `retrieval_min_score` (0.35), `reason_model` (`"reason.compose"`). See
  LLD 03 §9.2.
- **The compose LLM is never reached directly** — it rides the injected `gateway_client`
  (`make_retrieval(cfg, gateway_client)`). No `openai`/vendor SDK is imported in `retrieval/`; the
  gateway is the only place an OpenAI-compatible client is built (S5).
- **Dependencies:** `moss>=1.1.1`, `numpy>=1.26` (LLD 03 §9.3).
- **Add `product_id` to chunk metadata** for the product filter to bind — that's an *onboarding*
  (`ingest/`) change, flagged to Arham (LLD 03 §10.4); until then retrieval runs company-wide.

```bash
uv run python -m scripts.retrieval_mini --check                 # mock — no keys, CI-safe
uv run python -m scripts.retrieval_mini --check --real-compose  # drives the real gateway_compose path (mini gateway)
MOSS_PROJECT_ID=… MOSS_PROJECT_KEY=… \
  uv run python -m scripts.retrieval_mini --check --live        # real Moss against clutch-demo
```

### 2.2 Realtime & Voice (`src/realtime/` — LLD 05-realtime)

Needs **LiveKit** (transport) and, through the speech layer, **Cartesia** (STT/TTS).

| Service | Env var | Required for | Where to get it |
|---|---|---|---|
| **LiveKit** | `LIVEKIT_URL` | running the worker / `--check` | `wss://<project>.livekit.cloud`, or `ws://localhost:7880` (local docker) |
| | `LIVEKIT_API_KEY` | worker / token minting | LiveKit Cloud project (local docker dev key: `devkey`) |
| | `LIVEKIT_API_SECRET` | worker / token minting | LiveKit Cloud project (local docker dev secret: `secret`) |
| **Cartesia** | `CARTESIA_API_KEY` | STT/TTS in the loop | Cartesia dashboard (see §2.3) |

- **Config** (role-named slice `RealtimeConfig`, LLD 05-realtime §7): `stt_provider`/`tts_provider`
  (`"cartesia"`), `see_fps`, endpointing + barge-in toggles. No vendor names in config.
- **Dependencies:** `livekit-agents>=1.0`, `livekit-plugins-silero>=1.0`,
  `livekit-plugins-cartesia>=1.0` (+ optional `livekit-plugins-turn-detector>=1.0`).
- **Runtime infra (not a Python dep):** `docker` to run a local `livekit-server` for `--check`.
- **Keyless ⇒ SKIP:** if `LIVEKIT_*`/`CARTESIA_API_KEY` are absent, `--check` prints `SKIP (no
  creds)` and exits 0 (LLD 05-realtime §9.5).

```bash
uv run python -m scripts.realtime_mini --check    # boots real local LiveKit + worker + Cartesia; SKIP if no creds
uv run python -m scripts.realtime_mini --talk "how do I clear a jam"   # dev run-loop, one turn
```

### 2.3 Speech Layer (`src/speech/` — LLD 05-speech)

The STT/TTS swap point. **Cartesia is the default and only shipped impl**; selection works with no
keys, a real round-trip needs a Cartesia key.

| Service | Env var | Required? | Default | Purpose |
|---|---|---|---|---|
| **Cartesia** | `CARTESIA_API_KEY` | **yes** (for a real run) | — | auth; absent ⇒ `stream()` raises, `--check` SKIPs |
| | `CARTESIA_STT_MODEL` | no | `ink-whisper` | STT model id (alt `ink-2`) |
| | `CARTESIA_TTS_MODEL` | no | `sonic-3` | TTS model id (prefer `sonic-3.5` once verified) |
| | `CARTESIA_VOICE_ID` | no | plugin default | TTS voice id |
| | `CARTESIA_LANGUAGE` | no | `en` | STT + TTS language |

- **Config** (role-named): only `stt_provider` / `tts_provider` (both default `"cartesia"`). That's
  the entire config surface this layer reads (LLD 05-speech §7.1).
- **Dependencies (`voice` group):** `livekit-agents>=1.0`, `livekit-plugins-cartesia>=1.0`. Neither
  is imported at module top → *selection* is testable with nothing installed.
- **Adding a second vendor** (Deepgram, ElevenLabs, …) = one new `providers/*.py` + one `elif` in
  `get_stt`/`get_tts`; **no caller change** (00 §7). That is the plug-and-play guarantee.

```bash
uv run python scripts/speech_e2e.py --check    # pure selection checks (keyless) + real TTS→STT round-trip if key set
uv run python scripts/speech_e2e.py --raw      # optional raw-Cartesia-SDK vendor smoke (no LiveKit)
```

### 2.4 Shared gateway note (retrieval compose ↔ perception vision)

The compose/vision LLM is reached through **one OpenAI-compatible gateway client** (injected, not
constructed inside the Limbs). Two ways to provide it:

- **TrueFoundry gateway (intended, HLD 06):** `TRUEFOUNDRY_BASE_URL` + `TRUEFOUNDRY_API_KEY` back
  both `reason.compose` and perception's vision call (read by
  `src/perception/providers/qwen_gateway.py`). **A base URL was not provided yet**, so this path is
  not yet exercised.
- **OpenRouter (what works today):** `OPENROUTER_BASE_URL=https://openrouter.ai/api/v1` +
  `OPENROUTER_API_KEY` is OpenAI-compatible and was used for the live retrieval compose test with
  `REASON_MODEL=minimax/minimax-m2`. `openrouter.yaml` lists the available MiniMax/Qwen models.

`QWEN_VISION_MODEL` (default `qwen2.5-vl-72b-instruct`) is perception's knob. Switching from OpenRouter
to the TrueFoundry gateway is a **config/env change only** — no code edit in `retrieval/`.

---

## 3. Dependencies (what to install per piece)

The base install (`openai`, `pillow`, `python-dotenv`) runs every mock/selection path. Add a piece's
deps only when you want it live. Suggested `pyproject.toml` additions (the LLDs specify these):

```toml
# [project].dependencies — retrieval (LLD 03 §9.3)
"moss>=1.1.1"
"numpy>=1.26"

# [dependency-groups].voice — realtime + speech (LLD 05-realtime §7.1, 05-speech §7.3)
"livekit-agents>=1.0"
"livekit-plugins-silero>=1.0"            # VAD/endpointing (realtime)
"livekit-plugins-cartesia>=1.0"          # STT + TTS plugins
"livekit-plugins-turn-detector>=1.0"     # optional: better turn detection
```

```bash
uv sync                    # base deps only — mock paths run
uv sync --group voice      # add LiveKit + Cartesia plugins for realtime/speech
```

---

## 4. Quickstart — prove all three on a bare clone (no keys)

```bash
uv sync
uv run python -m scripts.retrieval_mini --check   # PASS (mock retrieve + template_compose)
uv run python scripts/speech_e2e.py --check       # PASS selection, SKIP round-trip (no Cartesia key)
uv run python -m scripts.realtime_mini --check    # SKIP (no LiveKit/Cartesia creds) — exits 0
```

All three exit 0 with **no secrets and no sibling section installed** — that is the proof each Limb is
"done + tested" alone (10 §6). Then add keys per §2 to escalate any piece to a real run.

---

## 5. `.env.example` (copy-paste)

The repo ships a committed [`.env.example`](../../../.env.example) at the root. Copy it and fill in
only what you need; `.env.local` is gitignored so secrets never get committed:

```bash
cp .env.example .env.local      # then edit .env.local
```

```dotenv
# ── Retrieval index (LLD 03) ──────────────────────────────────────────────
MOSS_PROJECT_ID=            # Moss portal — needed only for `retrieval_mini --live`
MOSS_PROJECT_KEY=

# ── Reasoning/vision gateway (HLD 06; retrieval compose + perception) ──────
# Option A — TrueFoundry gateway (intended; needs the base URL):
TRUEFOUNDRY_BASE_URL=       # gateway /openai endpoint (not yet provided)
TRUEFOUNDRY_API_KEY=
# Option B — OpenRouter (OpenAI-compatible; what the live compose test used):
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_API_KEY=
REASON_MODEL=minimax/minimax-m2             # compose model (see openrouter.yaml)
QWEN_VISION_MODEL=qwen2.5-vl-72b-instruct   # perception default (optional)

# ── LiveKit transport (LLD 05-realtime) ───────────────────────────────────
LIVEKIT_URL=wss://<project>.livekit.cloud   # or ws://localhost:7880 for local docker
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=

# ── Cartesia STT/TTS (LLD 05-speech) ──────────────────────────────────────
CARTESIA_API_KEY=
CARTESIA_STT_MODEL=ink-whisper     # optional (alt: ink-2)
CARTESIA_TTS_MODEL=sonic-3         # optional (prefer sonic-3.5 once verified)
CARTESIA_VOICE_ID=                 # optional — blank = plugin default voice
CARTESIA_LANGUAGE=en               # optional
```

---

## 6. Adding a new provider/vendor (the swap-point pattern)

Every Limb is built so a vendor swap is a **config value + one new file**, never a caller edit
(00 §7). The recipe, identical across the three:

1. **Add the impl** behind the existing Protocol — `src/retrieval/<backend>.py` (Retriever),
   `src/speech/providers/<vendor>.py` (STT/TTSProvider). Read its key from a new env var inside the
   file (lazy import of the vendor SDK, mirroring `qwen_gateway.py`).
2. **Register it** with one `elif` in the selector (`get_stt`/`get_tts`) or one branch in
   `make_retrieval` — keyed on a **role string** (`cfg.stt_provider == "deepgram"`).
3. **Document the env var** here (§2) and in `.env.example` (§5).
4. **Verify** with the same `--check` script — no new test lane.

Unknown provider names fail loud at startup (`ValueError`), so a typo is a boot error, not a silent
wrong-vendor pick.
