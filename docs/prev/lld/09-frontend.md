# Fixalong LLD 09 — Frontend / UX
**Implements:** HLD 09 · **Module(s):** `ui/` (Next.js, from `agent-react` scaffold) · **Build tier:** 0 (minimal) / 1 (polished) · **Status:** Draft v0.1

## 1. Responsibility
Render the live repair loop in the browser so the user can fix hands-free and judges can see the agent is grounded, fast, and safe. The UI is a **pure consumer** of typed JSON events the agent publishes over the LiveKit data channel (`context`, `state`, `hazard`, `receipt`) plus the existing `moss_context` event. It owns: camera viewfinder with part highlight, context card, **trust chip** (FACT verdict + source), **latency badge** (real Moss `time_taken_ms`), warning banner, repair-progress, push-to-talk + Stop, and the end-of-session repair receipt. It computes nothing about the repair — no retrieval, no FSM — it only parses, accumulates, and draws. Fail-safe display rule: never fabricate (no real latency → no badge; camera off → no viewfinder).

## 2. Files & public surface
Extends the scaffold (`agent-react`); paths relative to `ui/`.
```
hooks/
  useMossContextEvents.ts   # REUSED unchanged (moss_context → MossContextEvent[])
  useFixalongEvents.ts      # NEW — parse context|state|hazard|receipt → FixalongEvents
components/app/
  moss-match-card.tsx       # REUSED as base for ContextCard
  camera-viewfinder.tsx     # NEW — <CameraViewfinder>
  context-card.tsx          # NEW — <ContextCard> (match-card + trust chip + latency badge)
  trust-chip.tsx            # NEW — <TrustChip>
  latency-badge.tsx         # NEW — <LatencyBadge>
  warning-banner.tsx        # NEW — <WarningBanner>
  repair-progress.tsx       # NEW — <RepairProgress>
  repair-receipt.tsx        # NEW — <RepairReceipt>
  control-bar.tsx           # NEW — <ControlBar> (push-to-talk + Stop), wraps livekit control-bar
  session-view.tsx          # EDIT — compose the layout from HLD §2
lib/fixalong-events.ts      # NEW — TS types + parsers (single source of truth)
```
Public surface = the named React components above + `useFixalongEvents()` and the type exports in `lib/fixalong-events.ts`.

## 3. Dependencies
- **Libs:** `react`, `next`, `livekit-client` (`RoomEvent.DataReceived`), `@livekit/components-react` (`useRoomContext`, `useLocalParticipant`, `VideoTrack`), `@/lib/utils` (`cn`). Tailwind for styling (scaffold convention).
- **Internal:** reuses `useMossContextEvents`, `MossMatchCard`, scaffold `agent-control-bar`, `Button`, `cn`.
- **Env:** none new on the client; LiveKit token/URL already wired by the scaffold (`/api/connection-details`).
- **Agent-side contract:** event shapes mirror `agent/contracts.py` (LLD 05/11). `Verdict` and `Phase` are restated as TS literals (do not redefine semantics).

## 4. Data structures (TS — mirror the data-channel JSON; agent is the producer)
All events share the scaffold envelope `{ type: string; data: object }` and are decoded with one `TextDecoder`. Types live in `lib/fixalong-events.ts`.
```ts
export type Verdict = 'ACT' | 'HEDGE' | 'ESCALATE' | 'REFUSE';
export type Phase   = 'diagnosing' | 'guiding' | 'confirming' | 'warning' | 'done';

export interface SourceRef { title: string; section?: string; uri?: string; }  // e.g. "Manual" "§3.2"

// type:"context" — a grounded answer card (extends moss_context with verdict+source+part)
export interface ContextEvent {
  id: string;
  query: string;
  text: string;                       // the surfaced instruction/answer
  part?: string;                      // highlighted part name (joins the viewfinder)
  verdict: Verdict;                   // from FACT verify (HLD 11)
  source?: SourceRef;                 // provenance for the trust chip
  timeTakenMs: number | null;         // real Moss latency; null ⇒ no badge
  timestamp: number;                  // ms epoch
}

// type:"state" — repair FSM snapshot (LLD 05)
export interface StateEvent {
  phase: Phase;
  stepIndex: number;                  // 0-based current step
  totalSteps: number;                 // for ▣▣▢▢
  goal: string;
  currentNodeId: string;
  visibleParts: { name: string; bbox?: [number, number, number, number] }[]; // normalized 0..1
  timestamp: number;
}

// type:"hazard" — surface a warning (LLD 05/06)
export interface HazardEvent {
  id: string;
  message: string;                    // "Fuser may be hot"
  severity: 'warn' | 'critical';      // ⚠️ vs 🛑 banner color
  part?: string;
  timestamp: number;
}

// type:"receipt" — session closer on phase=done (HLD 09 §5)
export interface ReceiptEvent {
  problem: string;
  likelyCause: string;
  stepsCompleted: string[];
  warningsShown: string[];
  sources: { source: SourceRef; verdict: Verdict }[];
  timestamp: number;
}

export type FixalongEvent = ContextEvent | StateEvent | HazardEvent | ReceiptEvent;

export interface FixalongState {           // accumulated view model returned by the hook
  contexts: ContextEvent[];                // last N, newest last (cap 10, scaffold pattern)
  state: StateEvent | null;                // latest snapshot wins
  hazards: HazardEvent[];                  // active, deduped by id
  receipt: ReceiptEvent | null;
  lastLatencyMs: number | null;            // most recent real Moss number (for header badge)
}
```
Verdict → glyph/intent map (single source, used by trust chip + receipt):
```ts
export const VERDICT_UI: Record<Verdict, { glyph: string; label: string; tone: 'ok'|'warn'|'stop'|'block' }> = {
  ACT:      { glyph: '✅', label: 'verified',   tone: 'ok' },
  HEDGE:    { glyph: '⚠️', label: 'verify',     tone: 'warn' },
  ESCALATE: { glyph: '🛑', label: 'untrusted',  tone: 'stop' },
  REFUSE:   { glyph: '🚫', label: 'unverified', tone: 'block' },
};
```

## 5. Key functions
**`parseFixalong(payload: Uint8Array): FixalongEvent | null`** — mirror `useMossContextEvents.parsePayload`: decode → `JSON.parse` → switch on `message.type`. Per type, narrow fields with `typeof`/`Array.isArray` guards; return `null` on any missing required field (e.g. `context` without `query`, `state` without `totalSteps`). `time_taken_ms` maps to `timeTakenMs` only when `typeof === 'number'`, else `null`. Wrap in `try/catch`; on throw `console.warn` and return `null` (never crash the render).

**`reduceFixalong(prev: FixalongState, e: FixalongEvent): FixalongState`** — pure reducer:
- `context`: append to `contexts`, slice to last 10; if `e.timeTakenMs != null` set `lastLatencyMs`.
- `state`: replace `state` (latest wins).
- `hazard`: upsert into `hazards` by `id` (dedupe re-sends).
- `receipt`: set `receipt`.

**`useFixalongEvents(): FixalongState`** — `useRoomContext()`; `useState<FixalongState>(initial)`; in `useEffect`, subscribe `room.on(RoomEvent.DataReceived, h)` where `h(payload) = parse → if !null setState(s => reduceFixalong(s, e))`; cleanup `room.off`. Deps `[room]`. Returns memoized state. Runs **alongside** `useMossContextEvents` (different `type` strings; both ignore foreign payloads), so the scaffold's raw Moss panel keeps working.

**Component contracts (props):**
```ts
<CameraViewfinder parts={StateEvent['visibleParts']} enabled={boolean} />     // enabled=false ⇒ render null
<ContextCard event={ContextEvent} />                                          // wraps match-card body
<TrustChip verdict={Verdict} source?={SourceRef} />
<LatencyBadge ms={number | null} />                                           // ms==null ⇒ render null
<WarningBanner hazards={HazardEvent[]} />                                     // []==[] ⇒ render null
<RepairProgress stepIndex={number} totalSteps={number} phase={Phase} />
<RepairReceipt receipt={ReceiptEvent | null} />                              // null ⇒ render null
<ControlBar onStop={() => void} />                                            // push-to-talk = mic toggle
```

## 6. Control flow / sequence
1. Agent (LLD 05/11) publishes a typed JSON event over the data channel after each retrieve/verify/FSM tick.
2. `useFixalongEvents` `DataReceived` handler parses + reduces → new `FixalongState`.
3. `SessionView` reads the hook and renders the HLD §2 layout:
   - left: `<CameraViewfinder>` (enabled from `useLocalParticipant` camera track) + transcript strip + `<ControlBar>`;
   - right: `<WarningBanner>`, the newest `<ContextCard>` (carries `<TrustChip>` + `<LatencyBadge>`), `<RepairProgress>`.
4. `<CameraViewfinder>` overlays a highlight box per `state.visibleParts[].bbox` (matched by name to `context.part`).
5. On `phase==='done'` a `receipt` event arrives → `<RepairReceipt>` renders the closer; `<RepairProgress>` shows full bar.
6. Push-to-talk toggles the local mic track; **Stop** calls `onStop` → barge-in/disconnect handled by Voice (LLD 03).

## 7. Config & tuning
- `MAX_CONTEXTS = 10` (matches scaffold `MAX_EVENTS_DEFAULT`).
- Latency badge format `${ms.toFixed(0)} ms` (scaffold convention); colorize green `< 10ms` to dramatize the sub-10ms thesis.
- Highlight box: normalized bbox × video element rect; 2px solid `tone` color, label = part name.
- Hazard banner: `critical` = solid red + persistent; `warn` = amber, auto-dismiss after 8s unless re-sent.
- Card shows only the **latest** context prominently; older ones collapse into the scaffold Moss panel.

## 8. Error handling & fallbacks
- **Malformed/foreign payload:** parser returns `null`, ignored — render unaffected (mirrors scaffold).
- **No real latency** (`timeTakenMs == null`): `<LatencyBadge>` returns `null` — never show a fake number (HLD §7 credibility rule).
- **Camera/vision off** (no local video track): `<CameraViewfinder enabled={false}>` returns `null`; cards still render from voice-driven retrieval.
- **Data channel hiccup:** state is sticky — last `FixalongState` persists; voice continues regardless; nothing clears on silence.
- **Unknown verdict string:** treat as `REFUSE` (most conservative) so an unrecognized value never shows a green ✅.
- **Missing bbox:** part still listed in card; no highlight drawn (don't guess a box).

## 9. Latency / perf notes
- Pure client render; no network beyond LiveKit. Reducer is O(1) per event; `contexts` capped at 10.
- The latency badge reflects the **agent-measured** Moss `time_taken_ms`, not round-trip — keep it the real Moss number only.
- Highlight overlay recomputes on `resize` and on each `state` event (cheap; rAF-throttle if needed at tier 1).

## 10. Test plan
Vitest + React Testing Library; mock the LiveKit room/data channel.
- **Parsers (unit):** valid `context|state|hazard|receipt` JSON → correct narrowed object; missing required field → `null`; `time_taken_ms` absent → `timeTakenMs: null`; unknown `type` → `null`; malformed JSON → `null` (no throw).
- **`reduceFixalong` (unit):** context cap to 10; `lastLatencyMs` updates only on numeric latency; hazard dedupe by id; latest `state` wins; receipt set.
- **`useFixalongEvents` (hook):** fire mock `DataReceived` payloads via a fake room emitter → assert accumulated `FixalongState`; assert `off` on unmount.
- **Components (render w/ mock events):**
  - `<TrustChip>` renders ✅/⚠️/🛑/🚫 + source per verdict; unknown verdict → 🚫.
  - `<LatencyBadge ms={null}>` renders nothing; `ms={7}` → "7 ms".
  - `<CameraViewfinder enabled={false}>` renders nothing; `enabled` + bbox → highlight box with part label.
  - `<WarningBanner>` hidden when `[]`; `critical` → red persistent.
  - `<RepairProgress stepIndex=1 totalSteps=4>` → "step 2 of 4" + ▣▣▢▢.
  - `<RepairReceipt>` renders problem/cause/steps/warnings/verified sources from a mock `ReceiptEvent`.
- **Integration:** drive a scripted event sequence (context → hazard → state×N → receipt) through `SessionView` with a mock room; snapshot the demo layout at each phase.

## 11. Build checklist (Tier-0 first)
1. **(T0)** `lib/fixalong-events.ts`: types + `VERDICT_UI` + `parseFixalong` + `reduceFixalong` (clone scaffold parser style).
2. **(T0)** `useFixalongEvents.ts`: subscribe/reduce/cleanup; verify it coexists with `useMossContextEvents`.
3. **(T0)** `<TrustChip>` + `<LatencyBadge>`; fold into `<ContextCard>` (fork `moss-match-card`).
4. **(T0)** `<RepairProgress>` + `<WarningBanner>`; wire into `session-view.tsx` right column.
5. **(T0)** `<ControlBar>`: push-to-talk (mic toggle) + Stop → `onStop`.
6. **(T1)** `<CameraViewfinder>` with `VideoTrack` + normalized-bbox highlight overlay; `enabled` from local camera track.
7. **(T1)** `<RepairReceipt>` on `phase=done`; latency green-threshold styling; hazard auto-dismiss.
8. **(T1)** Tests per §10; snapshot the four demo phases.
