# Workstream 4 — Frontend & Infra

**Owner:** ____ · **Skills:** frontend / deploy · **Mission:** make the magic *visible* (the live
UI — trust chip + latency badge) and keep the whole thing runnable (env/secrets, single-instance
deploy, the demo machine).

## You own
- **LLDs:** [09 frontend](../lld/09-frontend.md), [08 infra](../lld/08-infra.md).
- **Modules:** `ui/` (Next.js/React — extend the `livekit-moss-vercel` `agent-react` scaffold),
  deployment + env/secrets wiring, optional AWS (S3/Bedrock/Nova Sonic, Tier 2).

## Interfaces you PRODUCE
```ts
// UI renders the agent→UI data-channel events (published by Part 3, shaped by Part 2):
ContextEvent  { id, query, text, part?, verdict, source?, timeTakenMs, timestamp }
StateEvent    { phase, stepIndex, totalSteps, goal, currentNodeId, visibleParts[] }
HazardEvent   { type, part, message }
ReceiptEvent  { problem, cause, steps[], warnings[], sources[] }
// components: CameraViewfinder, ContextCard, TrustChip, LatencyBadge, WarningBanner,
//             RepairProgress, PushToTalk+Stop, RepairReceipt
```
Plus: the **deploy/run** story (single instance/laptop running the LiveKit agent + orchestrator;
Moss cloud or local; UI dev server) and `.env` wiring everyone fills in.

## Interfaces you CONSUME (stub on hour 0)
- The four data-channel event JSON shapes above (mock them to build the UI without the live agent).
- From **Part 1/2** (via the data channel): `verdict` (string) + `timeTakenMs` for the chip + badge.

## External deps / keys
LiveKit client SDK (browser), the deploy target (laptop/container; AWS optional, Tier 2). You
own the consolidated `.env` template + secrets handoff so each part drops its keys in one place.

## Build order
- **Tier 0:** UI shell rendering **ContextCard + TrustChip + real LatencyBadge** from a mock (then
  live) data channel; the `.env` template + a one-command run for the whole stack.
- **Tier 1:** CameraViewfinder (part highlight), WarningBanner (on hazard), RepairProgress,
  PushToTalk + Stop; reuse the scaffold's `useMossContextEvents` parse pattern.
- **Tier 2:** RepairReceipt screen; deploy to a cloud instance; optional AWS S3/Bedrock/Nova Sonic.

## Correctness notes
- TrustChip renders the **string** verdict (`ACT/HEDGE/ESCALATE/REFUSE`) → ✅/⚠️/🛑/🚫 + source§.
- LatencyBadge must show the **real** Moss `timeTakenMs` — hide it rather than fake a number.
- Camera off → hide the viewfinder; the cards still render from voice-driven retrieval.

## Definition of done
The UI shows live context cards with the **real** latency + trust chip, a warning banner on
hazards, and a receipt at the end; and `git clone → fill .env → one command` brings up the full
demo. Have a **recorded fallback** of the canonical run.

## Your demo moment
Everything the audience *sees*: the **7 ms badge**, the **trust chip flipping to 🚫** on an
unverified fact, the red **warning banner** on the safety pivot, and the **repair receipt** close.
Plus: you own the **fallback recording** so wifi can't sink the demo.
