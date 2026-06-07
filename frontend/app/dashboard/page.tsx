"use client";

import Image from "next/image";
import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { useRouter } from "next/navigation";
import { onAuthStateChanged, signOut as fbSignOut, User } from "firebase/auth";
import { ref, uploadBytesResumable } from "firebase/storage";
import { auth, storage } from "../lib/firebase";
import { api } from "../lib/api";

type ProductFile = { id: string; name: string; size: number; file?: File };
type Product = {
  id: string;
  name: string;
  image: string | null;
  status?: string;
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
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [products, setProducts] = useState<Product[]>([]);
  const [embedKey, setEmbedKey] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState<
    null | { mode: "create" } | { mode: "edit"; id: string }
  >(null);
  const [debugProduct, setDebugProduct] = useState<Product | null>(null);

  useEffect(() => {
    const unsub = onAuthStateChanged(auth, (u) => {
      if (!u) router.push("/signin");
      else {
        setUser(u);
        loadData();
      }
    });
    return unsub;
  }, [router]);

  // Real-time polling: refresh every 3s when any product is still processing
  useEffect(() => {
    const activeStates = ["uploading", "parsing", "indexing", "uploaded"];
    const hasActive = products.some(p => activeStates.includes(p.status || ""));
    if (!hasActive || !user) return;
    const interval = setInterval(() => {
      loadData();
    }, 3000);
    return () => clearInterval(interval);
  }, [products, user]);

  const loadData = async () => {
    try {
      const [prodRes, embedRes] = await Promise.all([
        api.getProducts(),
        api.getEmbed().catch(() => ({ api_key: "" }))
      ]);
      setProducts(prodRes.products.map((p: any) => ({
        id: p.product_id,
        name: p.name,
        image: p.photo_url || null,
        status: p.status || "draft",
        files: p.docs || []
      })));
      if (embedRes?.api_key) setEmbedKey(embedRes.api_key);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const editing =
    modal?.mode === "edit"
      ? products.find((p) => p.id === modal.id) ?? null
      : null;

  const uploadFile = async (file: File, pid: string): Promise<string> => {
    const res = await api.uploadFile(pid, file);
    return res.storage_path;
  };

  const createProduct = async (
    name: string,
    imageFile: File | null,
    files: ProductFile[]
  ) => {
    // Optimistic UI
    const tempId = "temp-" + Date.now();
    const tempProduct: Product = {
      id: tempId,
      name,
      image: imageFile ? URL.createObjectURL(imageFile) : null,
      status: "uploading",
      files: files
    };
    setProducts(prev => [tempProduct, ...prev]);

    try {
      const pRes = await api.createProduct(name);
      const pid = pRes.product_id;
      
      const docEntries: any[] = [];
      let photoPath = "";

      const uploadPromises = [];

      if (imageFile) {
        const coverFile = new File([imageFile], "cover_" + imageFile.name, { type: imageFile.type });
        uploadPromises.push(uploadFile(coverFile, pid).then(path => { photoPath = path; }));
      }

      for (const f of files) {
        if (f.file) {
          uploadPromises.push(uploadFile(f.file, pid).then(sPath => {
            docEntries.push({ doc_id: "", doc_type: "other", storage_path: sPath });
          }));
        }
      }

      await Promise.all(uploadPromises);

      if (docEntries.length > 0 || photoPath) {
        await api.registerDocs(pid, docEntries, photoPath);
        await api.processProduct(pid);
      }
      
      await loadData();
    } catch (e) {
      console.error(e);
      alert("Failed to create product");
      setProducts(prev => prev.filter(p => p.id !== tempId));
    }
  };

  const updateProduct = async (
    id: string,
    name: string,
    imageFile: File | null,
    files: ProductFile[]
  ) => {
    setProducts(prev => prev.map(p => p.id === id ? {
      ...p,
      name,
      image: imageFile ? URL.createObjectURL(imageFile) : p.image
    } : p));

    try {
      await api.updateProduct(id, name);
      // Wait, we should also handle uploading new files/images on edit later.
      // But for now, we just update the name based on the backend API support.
      await loadData();
    } catch (e) {
      console.error(e);
      alert("Failed to update product");
      await loadData();
    }
  };

  const deleteProduct = async (id: string) => {
    setProducts(prev => prev.filter(p => p.id !== id));
    try {
      await api.deleteProduct(id);
      await loadData();
    } catch (e) {
      console.error(e);
      alert("Failed to delete product");
      await loadData();
    }
  };

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
        <TopNav user={user} onSignOut={() => fbSignOut(auth)} />
        <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col overflow-y-auto">
          {products.length === 0 ? (
            <EmptyState onAdd={() => setModal({ mode: "create" })} />
          ) : (
            <ProductList
              products={products}
              embedKey={embedKey}
              onAdd={() => setModal({ mode: "create" })}
              onEdit={(id) => setModal({ mode: "edit", id })}
              onDelete={deleteProduct}
              onDebug={(product) => setDebugProduct(product)}
            />
          )}
        </div>
      </div>

      {debugProduct && (
        <DebugModal product={debugProduct} onClose={() => setDebugProduct(null)} />
      )}

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

function TopNav({ user, onSignOut }: { user: User | null; onSignOut: () => void }) {
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
                  {user?.displayName || "User"}
                </p>
                <p
                  className="text-xs font-medium"
                  style={{ color: MUTED, letterSpacing: "-0.02em" }}
                >
                  {user?.email || ""}
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
  embedKey,
  onAdd,
  onEdit,
  onDelete,
  onDebug,
}: {
  products: Product[];
  embedKey: string;
  onAdd: () => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
  onDebug: (product: Product) => void;
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
      <EmbedBlock embedKey={embedKey} />
      <ul className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
        {products.map((p) => (
          <ProductCard
            key={p.id}
            product={p}
            onEdit={() => onEdit(p.id)}
            onDelete={() => onDelete(p.id)}
            onDebug={() => onDebug(p)}
            onTest={() => {
              if (p.status === "ready") {
                window.location.href = `/test/${embedKey}?product_id=${p.id}`;
              } else {
                alert("Product must be 'ready' to test.");
              }
            }}
          />
        ))}
      </ul>
    </div>
  );
}

const PIPELINE_STAGES = ["uploading", "uploaded", "parsing", "indexing", "ready"] as const;
const STAGE_LABELS: Record<string, string> = {
  uploading: "Uploading files…",
  uploaded: "Queued for processing…",
  draft: "Draft",
  parsing: "Extracting text from documents…",
  indexing: "Building search index (Moss)…",
  ready: "Live — ready to answer",
  error: "Processing failed",
};

function StatusBadge({ status }: { status: string }) {
  const isActive = ["uploading", "uploaded", "parsing", "indexing"].includes(status);
  const bgColor = status === "ready" ? "#E8F5E9" 
    : status === "error" ? "#FFEBEE" 
    : status === "uploading" ? "#FFF3E0" 
    : status === "parsing" ? "#F3E5F5" 
    : status === "indexing" ? "#E8EAF6" 
    : "#E3F2FD";
  const fgColor = status === "ready" ? "#2E7D32" 
    : status === "error" ? "#C62828" 
    : status === "uploading" ? "#E65100" 
    : status === "parsing" ? "#7B1FA2" 
    : status === "indexing" ? "#283593" 
    : "#1565C0";

  return (
    <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-bold" style={{ backgroundColor: bgColor, color: fgColor }}>
      {isActive && (
        <svg width="12" height="12" viewBox="0 0 24 24" style={{ animation: "spin 1s linear infinite" }}>
          <circle cx="12" cy="12" r="10" fill="none" stroke={fgColor} strokeWidth="3" strokeDasharray="31.4 31.4" strokeLinecap="round" />
        </svg>
      )}
      {status === "ready" && (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={fgColor} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
          <path d="M20 6L9 17l-5-5" />
        </svg>
      )}
      {status?.toUpperCase() || "DRAFT"}
    </div>
  );
}

function PipelineProgress({ status }: { status: string }) {
  const stageIndex = PIPELINE_STAGES.indexOf(status as any);
  if (stageIndex === -1 && status !== "error") return null;
  const label = STAGE_LABELS[status] || status;
  const progress = status === "error" ? 0 : status === "ready" ? 100 : ((stageIndex + 1) / PIPELINE_STAGES.length) * 100;
  const barColor = status === "error" ? "#EF5350" 
    : status === "ready" ? "#66BB6A" 
    : status === "parsing" ? "#AB47BC" 
    : status === "indexing" ? "#5C6BC0" 
    : "#FFA726";

  return (
    <div className="flex flex-col gap-1.5 mt-1">
      <div className="h-1.5 w-full rounded-full overflow-hidden" style={{ backgroundColor: "#F0F2F5" }}>
        <div 
          className="h-full rounded-full transition-all duration-700 ease-out"
          style={{ 
            width: `${progress}%`, 
            backgroundColor: barColor,
            ...(status !== "ready" && status !== "error" ? { animation: "pulse-bar 1.5s ease-in-out infinite" } : {}),
          }} 
        />
      </div>
      <p className="text-xs font-medium" style={{ color: MUTED, letterSpacing: "-0.01em" }}>{label}</p>
    </div>
  );
}

function ProductCard({
  product,
  onEdit,
  onDelete,
  onDebug,
  onTest,
}: {
  product: Product;
  onEdit: () => void;
  onDelete: () => void;
  onDebug: () => void;
  onTest: () => void;
}) {
  return (
    <li className="flex flex-col gap-4 rounded-2xl bg-white p-5 shadow-sm transition-shadow hover:shadow-md">
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes pulse-bar { 0%, 100% { opacity: 1; } 50% { opacity: 0.6; } }
      `}</style>
      <div
        className="flex aspect-[4/3] w-full items-center justify-center overflow-hidden rounded-xl cursor-pointer"
        style={{ backgroundColor: "#F4F6F8" }}
        onClick={onDebug}
      >
        {product.image ? (
          <img
            src={product.image}
            alt={product.name}
            className="h-full w-full object-cover transition-transform duration-500 hover:scale-105"
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
            className="transition-transform duration-500 hover:scale-110"
          >
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <circle cx="9" cy="9" r="2" />
            <path d="m21 15-5-5L5 21" />
          </svg>
        )}
      </div>
      <div className="flex justify-between items-start">
        <div className="min-w-0 flex-1">
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
        <StatusBadge status={product.status || "draft"} />
      </div>
      <PipelineProgress status={product.status || "draft"} />
      <div className="mt-auto flex items-center gap-2">
        <button
          type="button"
          onClick={onDebug}
          className="flex-1 rounded-lg px-3 py-2 text-sm font-semibold transition-colors hover:bg-slate-50"
          style={{ border: `1.5px solid ${SOFT}`, color: "#283593", letterSpacing: "-0.02em" }}
        >
          View Outputs
        </button>
        <button
          type="button"
          onClick={onTest}
          className="flex-1 rounded-lg px-3 py-2 text-sm font-semibold transition-colors hover:bg-green-50"
          style={{ border: `1.5px solid ${SOFT}`, color: "#2E7D32", letterSpacing: "-0.02em" }}
        >
          Test Widget
        </button>
        <button
          type="button"
          onClick={onEdit}
          className="flex-1 rounded-lg px-3 py-2 text-sm font-semibold transition-colors hover:bg-slate-50"
          style={{ border: `1.5px solid ${SOFT}`, color: INK, letterSpacing: "-0.02em" }}
        >
          Edit
        </button>
        <button
          type="button"
          onClick={onDelete}
          className="flex-1 rounded-lg px-3 py-2 text-sm font-semibold transition-colors hover:bg-red-50"
          style={{ border: `1.5px solid ${SOFT}`, color: "#C04A4A", letterSpacing: "-0.02em" }}
        >
          Delete
        </button>
      </div>
    </li>
  );
}

function DebugModal({ product, onClose }: { product: Product; onClose: () => void }) {
  const [tab, setTab] = useState<"pdf" | "unsiloed" | "moss">("pdf");
  const [outputs, setOutputs] = useState<{pdf_url?: string, unsiloed_data?: any, moss_data?: any} | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function fetchOutputs() {
      try {
        const out = await api.getProductOutputs(product.id);
        setOutputs(out);
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    }
    fetchOutputs();
  }, [product.id]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 backdrop-blur-sm" style={{ backgroundColor: "rgba(50, 72, 93, 0.45)" }} onClick={onClose}>
      <div className="w-full max-w-4xl h-[80vh] flex flex-col rounded-3xl bg-white overflow-hidden shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4 border-b" style={{ borderColor: SOFT }}>
          <div className="flex items-center gap-3">
            <h2 className="text-xl font-bold" style={{ color: INK, letterSpacing: "-0.03em" }}>Pipeline Outputs: {product.name}</h2>
            {loading && <span className="text-xs font-semibold text-blue-600 bg-blue-50 px-2 py-1 rounded-full animate-pulse">Fetching from R2...</span>}
          </div>
          <button onClick={onClose} className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-100 text-lg font-bold text-slate-500 hover:bg-slate-200 transition-colors">&times;</button>
        </div>
        <div className="flex border-b bg-gray-50 px-6 pt-2 gap-4" style={{ borderColor: SOFT }}>
          <button onClick={() => setTab("pdf")} className={`pb-3 pt-2 font-semibold text-sm border-b-[3px] transition-colors ${tab === "pdf" ? "border-blue-600 text-blue-600" : "border-transparent text-slate-500 hover:text-slate-700"}`}>PDF Preview</button>
          <button onClick={() => setTab("unsiloed")} className={`pb-3 pt-2 font-semibold text-sm border-b-[3px] transition-colors ${tab === "unsiloed" ? "border-purple-600 text-purple-600" : "border-transparent text-slate-500 hover:text-slate-700"}`}>Unsiloed (Raw Parse)</button>
          <button onClick={() => setTab("moss")} className={`pb-3 pt-2 font-semibold text-sm border-b-[3px] transition-colors ${tab === "moss" ? "border-indigo-600 text-indigo-600" : "border-transparent text-slate-500 hover:text-slate-700"}`}>Moss (Index Chunks)</button>
        </div>
        <div className="flex-1 overflow-hidden bg-slate-50 p-6">
          {tab === "pdf" && (
            <div className="h-full w-full rounded-2xl border-2 border-dashed border-slate-300 flex flex-col items-center justify-center bg-white shadow-sm overflow-hidden">
              {outputs?.pdf_url ? (
                <iframe src={outputs.pdf_url} className="w-full h-full" title="PDF Preview" />
              ) : (
                <>
                  <svg className="h-16 w-16 text-slate-300 mb-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
                  <p className="text-base font-semibold text-slate-600">No PDF available</p>
                  <p className="mt-1 text-sm text-slate-400 max-w-sm text-center">({product.files[0]?.name || "skop-deck.pdf"})<br/>Make sure a document is uploaded.</p>
                </>
              )}
            </div>
          )}
          {tab === "unsiloed" && (
            <div className="h-full w-full flex flex-col rounded-2xl bg-[#1E1E1E] overflow-hidden shadow-inner">
              <div className="px-4 py-2 bg-[#2D2D2D] border-b border-[#404040] flex items-center justify-between">
                <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">response.json</span>
                <span className="text-xs font-mono text-purple-400">VISION_API</span>
              </div>
              <pre className="flex-1 overflow-auto p-4 text-[13px] text-green-400 font-mono leading-relaxed">
                {outputs?.unsiloed_data ? JSON.stringify(outputs.unsiloed_data, null, 2) : (loading ? "Loading..." : "No Unsiloed parse data available for this product yet. Processing may not have completed.")}
              </pre>
            </div>
          )}
          {tab === "moss" && (
            <div className="h-full w-full flex flex-col rounded-2xl bg-[#1E1E1E] overflow-hidden shadow-inner">
              <div className="px-4 py-2 bg-[#2D2D2D] border-b border-[#404040] flex items-center justify-between">
                <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">upsert_payload.json</span>
                <span className="text-xs font-mono text-indigo-400">MOSS_SDK</span>
              </div>
              <pre className="flex-1 overflow-auto p-4 text-[13px] text-blue-400 font-mono leading-relaxed">
                {outputs?.moss_data ? JSON.stringify(outputs.moss_data, null, 2) : (loading ? "Loading..." : "No Moss chunks available for this product yet. Processing may not have completed.")}
              </pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function EmbedBlock({ embedKey }: { embedKey: string }) {
  const [copied, setCopied] = useState(false);
  const [origin, setOrigin] = useState("https://cdn.clutch.ai");

  useEffect(() => {
    if (typeof window !== "undefined") {
      setOrigin(window.location.origin);
    }
  }, []);

  const snippet = embedKey 
    ? `<script src="${origin}/widget.js" data-clutch-key="${embedKey}" data-api-url="${process.env.NEXT_PUBLIC_API_URL || 'https://clutch-production-backend.onrender.com/api'}"></script>` 
    : `<!-- Add a product first to generate your widget snippet -->`;

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
  onSave: (name: string, imageFile: File | null, files: ProductFile[]) => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [image, setImage] = useState<string | null>(initial?.image ?? null);
  const [files, setFiles] = useState<ProductFile[]>(initial?.files ?? []);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);

  const onImage = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setImageFile(file);
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
      file: f,
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
                    setImageFile(null);
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
            onClick={() => canSave && onSave(name.trim(), imageFile, files)}
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
