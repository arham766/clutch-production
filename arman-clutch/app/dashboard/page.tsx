"use client";

import Image from "next/image";
import { useEffect, useRef, useState, type ChangeEvent } from "react";

type ProductFile = { id: string; name: string; size: number };
type Product = {
  id: string;
  name: string;
  image: string | null;
  files: ProductFile[];
};

const INK = "#32485D";
const ACCENT = "#6291C0";
const SOFT = "#E6E8EB";
const MUTED = "#7A8BA0";

function uid() {
  return Math.random().toString(36).slice(2, 10);
}

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export default function Dashboard() {
  const [products, setProducts] = useState<Product[]>([]);
  const [modal, setModal] = useState<
    null | { mode: "create" } | { mode: "edit"; id: string }
  >(null);

  const editing =
    modal?.mode === "edit"
      ? products.find((p) => p.id === modal.id) ?? null
      : null;

  const createProduct = (
    name: string,
    image: string | null,
    files: ProductFile[]
  ) =>
    setProducts((prev) => [...prev, { id: uid(), name, image, files }]);

  const updateProduct = (
    id: string,
    name: string,
    image: string | null,
    files: ProductFile[]
  ) =>
    setProducts((prev) =>
      prev.map((p) => (p.id === id ? { ...p, name, image, files } : p))
    );

  const deleteProduct = (id: string) =>
    setProducts((prev) => prev.filter((p) => p.id !== id));

  return (
    <section className="h-dvh w-full bg-white p-[2vmin]">
      <div
        className="relative flex h-full w-full flex-col overflow-hidden rounded-[40px] px-8 pt-8 pb-8"
        style={{
          backgroundImage: [
            "radial-gradient(ellipse 100% 100% at 50% 50%, rgba(202, 223, 235, 0) 50%, rgba(202, 223, 235, 0.49) 100%)",
            "linear-gradient(to bottom, #5E8EBE 0%, #77A2CA 20%, #91BDDB 60%, #CCE0EB 100%)",
          ].join(", "),
        }}
      >
        <TopNav onSignOut={() => alert("Signed out")} />
        <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col overflow-y-auto">
          {products.length === 0 ? (
            <EmptyState onAdd={() => setModal({ mode: "create" })} />
          ) : (
            <ProductList
              products={products}
              onAdd={() => setModal({ mode: "create" })}
              onEdit={(id) => setModal({ mode: "edit", id })}
              onDelete={deleteProduct}
            />
          )}
        </div>
      </div>

      {modal && (
        <ProductModal
          initial={editing}
          onClose={() => setModal(null)}
          onSave={(name, image, files) => {
            if (modal.mode === "edit") updateProduct(modal.id, name, image, files);
            else createProduct(name, image, files);
            setModal(null);
          }}
        />
      )}
    </section>
  );
}

function TopNav({ onSignOut }: { onSignOut: () => void }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [menuOpen]);

  return (
    <div className="flex items-center justify-between text-white">
      <div className="flex items-center gap-2">
        <Image
          src="/clutch-logo.svg"
          alt="Clutch"
          width={32}
          height={27}
          priority
          style={{ filter: "brightness(0) invert(1)" }}
        />
        <span
          className="text-xl font-bold"
          style={{ letterSpacing: "-0.05em" }}
        >
          Clutch
        </span>
      </div>
      <div className="flex items-center gap-2">
        <div ref={menuRef} className="relative">
          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            className="flex h-10 w-10 items-center justify-center rounded-full bg-white text-sm font-bold"
            style={{ color: ACCENT, letterSpacing: "-0.02em" }}
            aria-label="Account"
          >
            A
          </button>
          {menuOpen && (
            <div
              className="absolute right-0 top-12 z-10 w-56 rounded-2xl bg-white p-1.5"
              style={{ boxShadow: "0 8px 28px rgba(50,72,93,0.18)" }}
            >
              <div className="px-3 py-2.5">
                <p
                  className="text-sm font-semibold"
                  style={{ color: INK, letterSpacing: "-0.02em" }}
                >
                  Arman
                </p>
                <p
                  className="text-xs font-medium"
                  style={{ color: MUTED, letterSpacing: "-0.02em" }}
                >
                  arman@clutch.dev
                </p>
              </div>
              <div className="h-px" style={{ backgroundColor: SOFT }} />
              <button
                type="button"
                onClick={onSignOut}
                className="block w-full rounded-xl px-3 py-2.5 text-left text-sm font-semibold hover:bg-zinc-50"
                style={{ color: "#C04A4A", letterSpacing: "-0.02em" }}
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function PlusButton({ size = 88, onClick }: { size?: number; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center justify-center rounded-full"
      style={{ backgroundColor: "rgba(255,255,255,0.2)", color: "white", width: size, height: size }}
      aria-label="Add product"
    >
      <svg
        width={size * 0.42}
        height={size * 0.42}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
      >
        <path d="M12 5v14M5 12h14" />
      </svg>
    </button>
  );
}

function EmptyState({ onAdd }: { onAdd: () => void }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center text-white">
      <PlusButton onClick={onAdd} />
      <p
        className="mt-6 text-lg font-semibold"
        style={{ letterSpacing: "-0.03em" }}
      >
        Add product
      </p>
      <p
        className="mt-1 text-sm font-medium text-white/85"
        style={{ letterSpacing: "-0.02em" }}
      >
        Upload manuals so Clutch can answer for it.
      </p>
    </div>
  );
}

function ProductList({
  products,
  onAdd,
  onEdit,
  onDelete,
}: {
  products: Product[];
  onAdd: () => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div className="flex h-full w-full flex-col pt-6">
      <div className="flex items-center justify-between">
        <h1
          className="text-3xl font-bold text-white"
          style={{ letterSpacing: "-0.05em" }}
        >
          My products
        </h1>
        <button
          type="button"
          onClick={onAdd}
          className="flex items-center gap-1.5 rounded-xl bg-white px-4 py-2.5 text-sm font-semibold"
          style={{ color: ACCENT, letterSpacing: "-0.02em" }}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
          >
            <path d="M12 5v14M5 12h14" />
          </svg>
          Add product
        </button>
      </div>
      <EmbedBlock />
      <ul className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
        {products.map((p) => (
          <ProductCard
            key={p.id}
            product={p}
            onEdit={() => onEdit(p.id)}
            onDelete={() => onDelete(p.id)}
          />
        ))}
      </ul>
    </div>
  );
}

function ProductCard({
  product,
  onEdit,
  onDelete,
}: {
  product: Product;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <li className="flex flex-col gap-4 rounded-2xl bg-white p-5">
      <div
        className="flex aspect-[4/3] w-full items-center justify-center overflow-hidden rounded-xl"
        style={{ backgroundColor: "#F4F6F8" }}
      >
        {product.image ? (
          <img
            src={product.image}
            alt={product.name}
            className="h-full w-full object-cover"
          />
        ) : (
          <svg
            width="40"
            height="40"
            viewBox="0 0 24 24"
            fill="none"
            stroke="#B1B8C2"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <circle cx="9" cy="9" r="2" />
            <path d="m21 15-5-5L5 21" />
          </svg>
        )}
      </div>
      <div className="min-w-0">
        <p
          className="truncate text-lg font-bold"
          style={{ color: INK, letterSpacing: "-0.03em" }}
        >
          {product.name}
        </p>
        <p
          className="mt-1 text-sm font-medium"
          style={{ color: MUTED, letterSpacing: "-0.02em" }}
        >
          {product.files.length} {product.files.length === 1 ? "file" : "files"}
        </p>
      </div>
      <div className="mt-auto flex items-center gap-2">
        <button
          type="button"
          onClick={onEdit}
          className="flex-1 rounded-lg px-3 py-2 text-sm font-semibold"
          style={{ border: `1.5px solid ${SOFT}`, color: INK, letterSpacing: "-0.02em" }}
        >
          Edit
        </button>
        <button
          type="button"
          onClick={onDelete}
          className="flex-1 rounded-lg px-3 py-2 text-sm font-semibold"
          style={{ border: `1.5px solid ${SOFT}`, color: "#C04A4A", letterSpacing: "-0.02em" }}
        >
          Delete
        </button>
      </div>
    </li>
  );
}

function EmbedBlock() {
  const [copied, setCopied] = useState(false);
  const snippet = `<script src="https://clutch.dev/embed.js" async></script>`;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // ignore
    }
  };

  return (
    <div className="mt-6 flex flex-col gap-3">
      <div className="px-1">
        <h2
          className="text-lg font-bold text-white"
          style={{ letterSpacing: "-0.03em" }}
        >
          Embed Clutch
        </h2>
        <p
          className="mt-1 text-sm font-medium text-white/85"
          style={{ letterSpacing: "-0.02em" }}
        >
          Paste this once on your support site. Clutch answers for every product you add here.
        </p>
      </div>
      <div
        className="relative rounded-2xl p-5 pr-16 backdrop-blur-md"
        style={{ backgroundColor: "rgba(255,255,255,0.18)" }}
      >
        <code
          className="block break-all font-mono text-xs leading-relaxed text-white"
        >
          {snippet}
        </code>
        <button
          type="button"
          onClick={copy}
          className="absolute top-1/2 right-3 flex h-8 -translate-y-1/2 items-center gap-1 rounded-md px-2.5 text-xs font-semibold"
          style={{
            backgroundColor: "rgba(255,255,255,0.85)",
            color: ACCENT,
            letterSpacing: "-0.02em",
          }}
          aria-label="Copy code"
        >
          {copied ? (
            <>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                <path d="M20 6L9 17l-5-5" />
              </svg>
              Copied
            </>
          ) : (
            <>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="9" y="9" width="13" height="13" rx="2" />
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
              </svg>
              Copy
            </>
          )}
        </button>
      </div>
    </div>
  );
}

function ProductModal({
  initial,
  onClose,
  onSave,
}: {
  initial: Product | null;
  onClose: () => void;
  onSave: (name: string, image: string | null, files: ProductFile[]) => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [image, setImage] = useState<string | null>(initial?.image ?? null);
  const [files, setFiles] = useState<ProductFile[]>(initial?.files ?? []);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);

  const onImage = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setImage(typeof reader.result === "string" ? reader.result : null);
    reader.readAsDataURL(file);
    if (imageInputRef.current) imageInputRef.current.value = "";
  };

  const onFiles = (e: ChangeEvent<HTMLInputElement>) => {
    const list = e.target.files;
    if (!list) return;
    const added = Array.from(list).map((f) => ({
      id: uid(),
      name: f.name,
      size: f.size,
    }));
    setFiles((prev) => [...prev, ...added]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const removeFile = (id: string) =>
    setFiles((prev) => prev.filter((f) => f.id !== id));

  const canSave = name.trim().length > 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: "rgba(50, 72, 93, 0.35)" }}
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-3xl bg-white p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h2
          className="text-xl font-bold"
          style={{ color: INK, letterSpacing: "-0.04em" }}
        >
          {initial ? "Edit product" : "Add product"}
        </h2>

        <div className="mt-5 flex flex-col gap-1.5">
          <label
            className="text-xs font-semibold"
            style={{ color: INK, letterSpacing: "-0.02em" }}
          >
            Name
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="HP LaserJet Pro M404n"
            className="h-11 w-full rounded-xl bg-white px-3.5 text-sm font-medium outline-none placeholder:font-medium"
            style={{
              border: `1.5px solid ${SOFT}`,
              color: INK,
              letterSpacing: "-0.02em",
            }}
          />
        </div>

        <div className="mt-4 flex items-stretch gap-2">
          <button
            type="button"
            onClick={() => imageInputRef.current?.click()}
            className="relative flex h-20 w-20 flex-shrink-0 items-center justify-center overflow-hidden rounded-xl"
            style={{
              border: `1.5px ${image ? "solid" : "dashed"} ${SOFT}`,
              backgroundColor: "#F4F6F8",
            }}
            aria-label="Upload product image"
          >
            {image ? (
              <>
                <img src={image} alt="" className="h-full w-full object-cover" />
                <span
                  role="button"
                  tabIndex={0}
                  onClick={(e) => {
                    e.stopPropagation();
                    setImage(null);
                  }}
                  className="absolute top-1 right-1 flex h-5 w-5 cursor-pointer items-center justify-center rounded-full bg-white text-xs font-bold"
                  style={{ color: "#C04A4A" }}
                  aria-label="Remove image"
                >
                  ×
                </span>
              </>
            ) : (
              <svg
                width="22"
                height="22"
                viewBox="0 0 24 24"
                fill="none"
                stroke="#B1B8C2"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <circle cx="9" cy="9" r="2" />
                <path d="m21 15-5-5L5 21" />
              </svg>
            )}
          </button>
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="flex h-20 flex-1 flex-col items-center justify-center rounded-xl text-sm font-semibold"
            style={{
              border: `1.5px dashed ${SOFT}`,
              color: MUTED,
              letterSpacing: "-0.02em",
            }}
          >
            Click to upload documents
            <span className="text-xs font-medium">PDF, manuals, spec sheets</span>
          </button>
          <input
            ref={imageInputRef}
            type="file"
            accept="image/*"
            onChange={onImage}
            className="hidden"
          />
          <input
            ref={fileInputRef}
            type="file"
            multiple
            onChange={onFiles}
            className="hidden"
          />
        </div>

        <div className="mt-3 flex flex-col gap-1.5">
          {files.length > 0 && (
            <ul className="mt-2 flex flex-col gap-1.5">
              {files.map((f) => (
                <li
                  key={f.id}
                  className="flex items-center justify-between rounded-lg px-3 py-2"
                  style={{ backgroundColor: "#F4F6F8" }}
                >
                  <div className="min-w-0 flex-1">
                    <p
                      className="truncate text-sm font-semibold"
                      style={{ color: INK, letterSpacing: "-0.02em" }}
                    >
                      {f.name}
                    </p>
                    <p
                      className="text-xs font-medium"
                      style={{ color: MUTED, letterSpacing: "-0.02em" }}
                    >
                      {formatBytes(f.size)}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeFile(f.id)}
                    className="ml-3 text-xs font-semibold"
                    style={{ color: "#C04A4A", letterSpacing: "-0.02em" }}
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="mt-6 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl px-4 py-2.5 text-sm font-semibold"
            style={{
              border: `1.5px solid ${SOFT}`,
              color: INK,
              letterSpacing: "-0.02em",
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => canSave && onSave(name.trim(), image, files)}
            disabled={!canSave}
            className="rounded-xl px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-40"
            style={{ backgroundColor: ACCENT, letterSpacing: "-0.02em" }}
          >
            {initial ? "Save" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}
