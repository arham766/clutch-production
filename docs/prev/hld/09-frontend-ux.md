# Fixalong HLD 09 — Frontend / UX

**Status:** Draft v0.1 · **Build tier:** 0 (minimal) / 1 (polished) · **Sponsor:** —
**Depends on:** all (renders the loop) · **Consumed by:** the user + the judges

---

## 1. Purpose

Make the live loop **visible**. The UX must do two jobs at once: help the *user* fix the
thing hands-free, and let *judges* see that the agent is grounded, fast, and safe. The
"magic" is invisible unless we surface retrieval + verification on screen.

## 2. Layout

```
 ┌───────────────────────────── Fixalong ──────────────────────────────┐
 │  [ camera viewfinder ]            │  Context (live)                  │
 │   live frames; part highlight     │  ┌────────────────────────────┐ │
 │   (Vision, 04)                    │  │ Pickup roller   ✅ §3.2  7ms│ │ ← context card
 │                                   │  │ clean w/ IPA; avoid transfer│ │   + trust chip
 │                                   │  └────────────────────────────┘ │   + latency badge
 │                                   │  ⚠️ Fuser may be hot (warning)   │
 │  ───────────────────────────────  │                                  │
 │  transcript / state strip         │  Repair progress: ▣▣▢▢          │
 │  "looks shiny" → pickup roller    │  step 2 of 4                     │
 │  [ push-to-talk ]  [ stop 🛑 ]     │                                  │
 └──────────────────────────────────────────────────────────────────────┘
```

## 3. Key elements

| Element | Source | Why it matters |
|---|---|---|
| **Camera viewfinder** + part highlight | Vision (04) | shows the system sees what the user sees |
| **Context cards** (step / part / diagram) | Retrieval (02) | shows grounded, manual-sourced answers |
| **Trust chip** ✅/⚠️/🛑/🚫 + source (§3.2) | FACT gate (06) | proves provenance — *this came from the real manual* |
| **Latency badge** ("7 ms") | Moss (02) | makes the sub-10 ms thesis literal and undeniable |
| **Warning banner** (red, on hazard) | State Machine (05) | safety is visible, not just spoken |
| **Repair progress** | State Machine (05) | shows live state tracking, not one-shot Q&A |
| **Push-to-talk + Stop** | Voice (03) | fallback control when STT/turn-taking struggles |
| **Repair receipt** (end) | §5 | the "enterprise-ready" closer |

## 4. The two chips that win the demo
1. **Latency badge** — render Moss's real `time_taken_ms` (the scaffold already publishes
   this over the LiveKit data channel). Must be the *real* number.
2. **Trust chip** — render the FACT `Verdict` (from **HLD 11** verify) + source section:
   ✅ verified (ACT) · ⚠️ stale/verify (HEDGE) · 🛑 untrusted (ESCALATE) · 🚫 unverified (REFUSE).
   The poisoned/stale-fact beats light this up.

## 5. Repair receipt (session end)
Generated on `phase=done` (05). Feels enterprise-ready and is a clean demo closer:

```
Problem: Paper jam persisted after visible paper removed.
Likely cause: Torn fragment near rear paper path + glossy pickup roller.
Steps completed:
  1. Opened rear access panel.
  2. Removed torn paper fragment.
  3. Cleaned pickup roller.
Warnings shown:  Avoid fuser area · Avoid touching transfer roller.
Sources (verified):  Manual §3.2 ✅ · Rear paper-path diagram ✅
```

## 6. Implementation
- Start from the `livekit-moss-vercel` `agent-react` frontend (already renders context
  cards + latency badge from the data channel). Add: camera viewfinder, trust chip, warning
  banner, progress, receipt.
- State pushed agent→UI over the LiveKit data channel as typed events
  (`context`, `state`, `hazard`, `receipt`).

## 7. Failure modes & fallbacks
| Failure | Fallback |
|---|---|
| Camera/vision off | hide viewfinder; cards still render from voice-driven retrieval |
| Data channel hiccup | UI polls last-known state; voice continues regardless |
| Latency number missing | hide badge rather than show a fake number (credibility) |

## 8. Open questions
- Mobile-web (phone camera) vs desktop demo. Phone is the authentic form factor; desktop is
  easier to drive on a projector. Decide based on demo staging.
- How prominent to make the trust chip — it's a core differentiator, lean visible.
