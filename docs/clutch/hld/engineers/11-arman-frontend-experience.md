# Section A — Arman · Experience Layer (Front-End & Design)

**Engineer:** Arman — Front-End & Design Engineer (wizard at user-use-case visualization)
**Owns HLDs:** [05 — Realtime, Voice & Widget](../05-realtime-voice-widget.md) (**client side**) · [01 — Onboarding](../01-onboarding-corpus.md) (**console frontend**)
**Owns directories:** `console/` (company admin app) · `widget/` (customer embed widget) — both exclusive
**Builds on:** Firebase (Auth + Storage) · the `livekit-moss-vercel` / `agent-react` scaffold in `moss-research/`
**Seams you own:** **S9** (Console UI ⇄ Console API) · **S1** (Browser ⇄ Realtime) — see [10 §4](10-work-division-and-fusing.md)

> **Independence ([10 §5](10-work-division-and-fusing.md)).** Both your apps are self-contained. The
> **console** talks to Firebase Auth/Storage + Arham's REST API (`/api/*`); build it against a
> **`mock_api`** (canned products/status/embed) so you never wait on the backend. The **widget**
> renders the `src/events.py` event schema; build it against **`mock_transport`** (scripted events +
> stub token). **Connect later:** point `fetch` at Arham's real `/api/*` and flip `mock_transport` →
> the live LiveKit channel — zero UI rewrite.

---

## 1. Your mission
You build **everything a human looks at** — two front-ends:
1. **The Console** (company admin app, `console/`) — the self-serve onboarding flow: a company signs
   up, uploads its product docs into folders, watches them process, and copies the embed snippet.
   *This is the cold path that creates a tenant.*
2. **The Widget** (customer embed, `widget/`) — the one-line support widget end-customers use:
   **Type / Talk / See**, grounded answers, the "point your camera and I'll see it" moment.

You own *how both look and feel*. You own **no** business logic: the console's auth/storage/processing
is Arham's API; the widget's dialogue is Tonmoy's Agent and its voice/video is Fardin's server. You
call APIs and render results.

---

# PART 1 — The Console (company onboarding app)

## 2. The end-to-end workflow (what the company does)
```
Landing page (/)                → marketing + "Sign in" / "Sign up"
  └─ Sign up / Sign in          → Firebase Auth (email+password; Google optional)
       └─ first login           → POST /api/companies/bootstrap  → { company_id }
Dashboard (/dashboard)          → list of PRODUCT FOLDERS + "New product"
  └─ New product                → POST /api/products {name, brand}  → { product_id }
Product folder (/products/:id)  → drag-drop DOCS + one reference PHOTO into the folder
  ├─ upload files               → straight to Firebase Storage (per-company path)
  ├─ register them              → POST /api/products/:id/docs {docs[], photo_path}
  └─ "Process"                  → POST /api/products/:id/process  → { job_id }
Processing                      → poll GET /api/products/:id/status until "ready"
  status: draft→uploading→parsing→indexing→ready  (show a progress bar per doc)
Done                            → GET /api/embed → { api_key, snippet }
  └─ show a COPY box:           <script src=".../widget.js" data-clutch-key="<api_key>"></script>
```

## 3. The screens you build (`console/`)
| Route | Screen | Key UI |
|---|---|---|
| `/` | **Landing** | hero, value prop, **Sign in / Sign up** CTAs |
| `/signup`, `/login` | **Auth** | Firebase email+password form (+ "Continue with Google"); error states |
| `/dashboard` | **Product folders** | grid/list of products with per-product **status chip** (draft / processing / ready), "New product" button; auth-guarded |
| `/products/:id` | **Folder view** | folder header (name/brand), **drag-and-drop doc uploader**, **reference-photo uploader**, per-file rows with `doc_type` selector + parse status, **"Process" button** |
| `/products/:id` (processing) | **Progress** | live status bar (poll `/status`), per-doc parsing indicators, error surfacing |
| `/embed` (or modal) | **Embed code** | the `<script>` snippet in a **copy-to-clipboard** box + "paste this on your support page" instructions |

## 4. Auth — Firebase (client side, yours)
- Initialize the Firebase JS SDK in `console/` (config from env).
- **Sign up / sign in** with `createUserWithEmailAndPassword` / `signInWithEmailAndPassword` (and
  optionally `signInWithPopup(GoogleAuthProvider)`).
- After auth, get the **ID token** (`user.getIdToken()`) and attach it to **every** API call as
  `Authorization: Bearer <idToken>`. Refresh on expiry.
- **Route guard:** `/dashboard` and `/products/*` redirect to `/login` when no user; on 401 from the
  API, sign out and bounce to `/login`. (Token *verification* is Arham's — you only attach it.)
- On first successful login, call `POST /api/companies/bootstrap` once to ensure the tenant exists.

## 5. Uploading docs — the mechanism (the part to get right)
1. User drops files into a product folder. For each file you pick a `doc_type`
   (user_guide / service_manual / spec_sheet / troubleshooting / other) and mark one image as the
   **reference photo**.
2. **Upload the bytes straight to Firebase Storage** under the company-scoped path
   `companies/{company_id}/products/{product_id}/{filename}` (Storage security rules scope writes to
   the signed-in owner). Show per-file upload progress from the Storage SDK.
3. Once uploads finish, **register** them with the backend so it knows what to process:
   `POST /api/products/:id/docs { docs: [{doc_id, doc_type, storage_path}], photo_path }`.
4. Hit **Process** → `POST /api/products/:id/process` → then poll `/status`.

> Why Storage-direct (not multipart through the API): large PDFs don't tunnel through the API server,
> uploads resume, and Arham's backend just reads the paths from Storage. (Multipart-to-backend is the
> fallback if Storage rules are a hassle — same `/docs` contract.)

## 6. Console — Definition of done
- [ ] Landing → sign up → sign in works on a real Firebase project; session persists on reload.
- [ ] Auth guard + `Authorization: Bearer` on every `/api/*` call; 401 → login.
- [ ] Create product folders; list them with live status chips.
- [ ] Drag-drop upload to Firebase Storage with progress; `doc_type` per file + one reference photo.
- [ ] Register (`/docs`) + process (`/process`) + poll (`/status`) drive a visible progress UI.
- [ ] On "ready", the embed snippet renders in a copy box and copies cleanly.
- [ ] Whole flow runs against `mock_api` with **zero** code change when pointed at the real `/api/*`.

---

# PART 2 — The Widget (customer embed)

## 7. Scope
- The **embed snippet** + bootstrap: `<script src=".../widget.js" data-clutch-key="...">` → mounts the
  widget (iframe vs inline — 05 open question; default iframe for isolation).
- **Widget UI**, three modes in one shell: text transcript, **cited-answer cards** (doc/section/score),
  **confirm prompt** ("Is this the *X* or the *Y*?"), **voice-state indicator**, **camera viewfinder**,
  **latency badge**.
- **Modality transitions** — Type→Talk→See reuses the same session; surface the Agent's "Hit See and
  show me" nudge as a tappable control.
- Capture user input (text/intents) + publish **media tracks** (mic for Talk, camera for See). You
  publish the *track*; Fardin's server samples frames — you never extract frames yourself.
- Bootstrap: exchange `data-clutch-key` for a LiveKit token via `POST /connection-details` (Arham).

**Out:** what to say (Tonmoy 04); STT/TTS + LiveKit loop + frame sampling (Fardin 05-server); token
minting + tenancy (Arham 06); retrieval (Fardin 03).

## 8. Widget — build against mocks (day 1)
Build the entire widget against a **mock event source** emitting the Spine schema:
```ts
// widget/src/events.ts — MIRROR of src/events.py (Spine, 10 §3). Do not diverge.
type AnswerEvent  = { type: "answer";  data: { text: string; citations: {doc:string;section:string;score:number}[] } }
type ConfirmEvent = { type: "confirm"; data: { prompt: string; options: string[] } }
type VoiceState   = { type: "voice_state"; data: { state: "listening"|"speaking"|"thinking" } }
type Latency      = { type: "latency"; data: { time_taken_ms: number } }
```
`mock_transport` replays a scripted session (greeting → cited answer → See nudge → confirm → steps).

## 9. Widget — Definition of done
- [ ] Embed snippet mounts the widget from a single `data-clutch-key` line on a blank page.
- [ ] All four events render correctly from `mock_transport`, including multi-citation cards.
- [ ] Type, Talk, See reachable in one session; escalation nudge is a working control.
- [ ] Camera viewfinder publishes a video track; mic publishes audio (verified in a LiveKit room).
- [ ] Voice-state + latency badge wired (badge **hidden** when number missing — never fake it, 05 §6).
- [ ] UI fallbacks: STT failure → push-to-talk/Type; camera unavailable → hide viewfinder, cards still render.
- [ ] Flipping `mock_transport` → live data channel needs **zero** UI changes.

---

## 10. The single paths you connect through
- **S9 — Console UI ⇄ Console API (with Arham):** the REST surface `/api/*` (§2, §5), every call
  carrying the Firebase ID token. This is your only wire from the console to the backend.
- **S1 — Browser ⇄ Realtime (with Fardin):** *down* you render the four typed events over the LiveKit
  data channel; *up* you publish text/intents + mic/camera tracks; *bootstrap* via
  `POST /connection-details {company_key, modality} -> {token, url}` (Arham).
- Firebase Auth/Storage are shared dependencies (your client SDK side; Arham's admin side).

Need something off-seam? Raise it to extend the seam (10 §4) — no side-channels.

## 11. Handoffs
- **From Arham:** the `/api/*` contract + Firebase project config (Auth + Storage rules); the
  `/connection-details` route + company-key→token contract.
- **From Fardin:** the live data-channel event stream + track subscription; the authoritative
  `src/events.py` you mirror in `events.ts`.
- **To everyone:** the frozen `events.ts` mirror — schema changes are a Spine PR (10 §3).

## 12. Stretch / open questions
Console: product editing/versioning (re-upload, swap photo, 01 §8); multi-user per company.
Widget: iframe vs inline mount; mobile-web camera for authentic "See"; brand-color theming.
