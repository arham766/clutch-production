# Clutch — Backend Integration Guide

This document tells the backend team exactly what to build and where to wire it in.

> **DO NOT edit any frontend visual code.** Do not change JSX structure, Tailwind classes, inline styles, colors, fonts, spacing, copy, or any other rendering. Wire APIs into the existing handlers only. **Visual code is owned by the frontend team.**

---

## 1. What you're wiring up

| Feature | Where it's triggered on the frontend |
|---|---|
| Sign up (email + password) | `app/(auth)/signup/page.tsx` form submit |
| Sign in (email + password) | `app/(auth)/signin/page.tsx` form submit |
| Sign up / Sign in with Google | Google buttons in both auth pages |
| Forgot password (send reset link) | `app/(auth)/forgot-password/page.tsx` form submit |
| Sign out | "Sign out" item in the account dropdown (`TopNav` inside `app/dashboard/page.tsx`) |
| List products | Initial load of `app/dashboard/page.tsx` (replaces `useState<Product[]>([])`) |
| Create product | `ProductModal` save handler when `modal.mode === "create"` |
| Edit product | `ProductModal` save handler when `modal.mode === "edit"` |
| Delete product | Delete button inside each `ProductCard` |
| Embed snippet | The snippet rendered by `EmbedBlock` must reference the authenticated user's account |

Again: **do not change any of the rendering code.** Only replace local state mutations / placeholder handlers with API calls.

---

## 2. Auth

### Sign up
- **Endpoint:** `POST /api/auth/signup`
- **Body:** `{ name: string, email: string, password: string }`
- **Response (200):** sets session cookie, returns `{ user: { id, name, email } }`
- **Errors:** 400 invalid input, 409 email already exists

### Sign in
- **Endpoint:** `POST /api/auth/signin`
- **Body:** `{ email: string, password: string }`
- **Response (200):** sets session cookie, returns `{ user: { id, name, email } }`
- **Errors:** 401 invalid credentials

### Google OAuth
- Routes: `GET /api/auth/google/start` → redirect to Google, `GET /api/auth/google/callback` → set session, redirect to `/dashboard`
- The Google buttons should `window.location` to `/api/auth/google/start` — wire that into the existing button's `onClick`.

### Sign out
- **Endpoint:** `POST /api/auth/signout`
- Clears the session cookie. Frontend redirects to `/signin` after success.

### Forgot password
- **Endpoint:** `POST /api/auth/forgot-password`
- **Body:** `{ email: string }`
- **Response (200):** always 200 (never reveal if email exists)
- **Side effect:** email user a one-time reset link with a signed token (e.g. `/reset-password?token=…`).
- A reset-confirm endpoint will be needed later: `POST /api/auth/reset-password { token, password }`. The page for that doesn't exist yet — coordinate with frontend before adding it.

### Session
- Use HTTP-only, Secure, SameSite=Lax cookies.
- Provide `GET /api/auth/me` → `{ user } | 401`. Dashboard should call this on mount; if 401, redirect to `/signin`.

> **Again:** do not edit visual code. The forms already have inputs with `type="email"` / `type="password"` and labels. Wire `onSubmit` to call these endpoints. That is the entire scope.

---

## 3. Products

Frontend type shapes (must be preserved exactly):

```ts
type ProductFile = { id: string; name: string; size: number };
type Product = {
  id: string;
  name: string;
  image: string | null;   // URL when persisted (was data URL in client-only mode)
  files: ProductFile[];
};
```

When persisted, `image` becomes a **URL** to the stored image (not a data URL). `files[i]` will also need a download URL — add a `url` field server-side and the frontend team will surface it when needed.

### List products
- **Endpoint:** `GET /api/products`
- **Response:** `{ products: Product[] }`
- Replaces `useState<Product[]>([])` initial load in `app/dashboard/page.tsx`.

### Create product
- **Endpoint:** `POST /api/products` (multipart/form-data)
- **Form fields:**
  - `name`: string
  - `image`: file (optional, single image)
  - `files`: file[] (zero or more documents — PDFs, manuals, spec sheets)
- **Response:** `{ product: Product }`
- Frontend currently calls `createProduct(name, image, files)` with `image` as a data URL and `files` as `{ id, name, size }[]`. The backend team needs to:
  1. Change the frontend handler to send the actual `File` objects from the modal's `<input type="file">` instead of the stripped metadata.
  2. Upload to object storage (S3/GCS/R2 — pick one), store URLs in DB.
  
  Coordinate this small handler change with the frontend team. **Do not restructure the modal UI.**

### Update product
- **Endpoint:** `PUT /api/products/:id` (multipart/form-data)
- **Form fields:**
  - `name`: string
  - `image`: file (optional — present = replace, absent = unchanged)
  - `removeImage`: `"true"` (optional — explicit removal)
  - `newFiles`: file[] (newly added documents)
  - `removedFileIds`: string[] (ids of existing files to delete)
- **Response:** `{ product: Product }`

### Delete product
- **Endpoint:** `DELETE /api/products/:id`
- **Response:** 204
- Also delete the associated image + files from object storage.

---

## 4. Storage

### Database (suggested schema — Postgres)

```sql
create table users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  name text not null,
  password_hash text,        -- null for Google-only accounts
  google_sub text unique,    -- null for password-only accounts
  created_at timestamptz not null default now()
);

create table sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);

create table products (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  name text not null,
  image_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table product_files (
  id uuid primary key default gen_random_uuid(),
  product_id uuid not null references products(id) on delete cascade,
  name text not null,
  size bigint not null,
  url text not null,         -- object storage URL
  storage_key text not null, -- object storage key for deletion
  created_at timestamptz not null default now()
);
```

### Object storage
- One bucket. Path layout: `users/{userId}/products/{productId}/image/{filename}` and `users/{userId}/products/{productId}/files/{fileId}-{filename}`.
- Generate signed download URLs for `image_url` and `product_files.url` — or serve via a proxy endpoint.

---

## 5. Embed snippet

The dashboard renders one snippet for all products:

```html
<script src="https://clutch.dev/embed.js" async></script>
```

This is hardcoded in `EmbedBlock` in `app/dashboard/page.tsx`. The script will need to authenticate the embedding site to the right account — likely by a public account key.

Add an account-level public key to the user record (`users.embed_public_key`), and update the snippet string the frontend renders so it includes it, e.g.:

```html
<script src="https://clutch.dev/embed.js" data-key="pk_…" async></script>
```

**Do not change the visual treatment of the snippet block.** Only change the string content the snippet variable holds. Coordinate with the frontend team for the one-line change.

---

## 6. Frontend handler touchpoints (the only places to edit)

| File | What to replace |
|---|---|
| `app/(auth)/signup/page.tsx` | Add `onSubmit` on the `<form>`. Call `POST /api/auth/signup`. On 200, `router.push("/dashboard")`. On error, show inline error. |
| `app/(auth)/signin/page.tsx` | Same shape — `POST /api/auth/signin`. |
| `app/(auth)/forgot-password/page.tsx` | Same shape — `POST /api/auth/forgot-password`. Show success state. |
| `app/(auth)/_components/AuthForm.tsx` | Wire `GoogleButton` `onClick` → `window.location.href = "/api/auth/google/start"`. |
| `app/dashboard/page.tsx` — `Dashboard` | Replace `useState<Product[]>([])` with a `useEffect` that calls `GET /api/products`. Add `GET /api/auth/me` redirect-if-401. |
| `app/dashboard/page.tsx` — `createProduct` / `updateProduct` / `deleteProduct` | Call corresponding API endpoints. Re-fetch or update local state from the API response. |
| `app/dashboard/page.tsx` — `ProductModal` `onFiles` / `onImage` | Change so the modal stores the underlying `File` objects (not stripped metadata + data URL) so they can be sent as multipart in the save handler. |
| `app/dashboard/page.tsx` — `TopNav` "Sign out" button | Call `POST /api/auth/signout`, then `router.push("/signin")`. |

That's the full surface area. **Anything else is off-limits.**

---

## 7. Rules — final reminder

1. **Do not edit visual / rendering code.** No changes to JSX, classes, styles, copy, fonts, spacing.
2. **Do not add UI components, modals, toasts, error boundaries, or empty states** unless explicitly approved by frontend.
3. **Do not restructure files** beyond what's listed in section 6.
4. **Do not add dependencies that affect the frontend bundle** without coordinating.
5. **Ask before adding loading skeletons, spinners, or any visual feedback.** Frontend will decide where these go.

Wire the data, then stop. The visual layer is done.
