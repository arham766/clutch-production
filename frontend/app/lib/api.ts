import { auth } from "./firebase";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

async function fetchWithAuth(path: string, options: RequestInit & { customToken?: string } = {}) {
  const { customToken, ...fetchOptions } = options;
  const user = auth.currentUser;
  // If user isn't logged in, we shouldn't be making API calls to our backend really
  // But we try to get the token anyway (this handles refresh automatically)
  const token = customToken || (user ? await user.getIdToken() : null);
  
  const headers = {
    ...fetchOptions.headers,
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };

  const res = await fetch(`${API_BASE}${path}`, { ...fetchOptions, headers });
  
  if (!res.ok) {
    if (res.status === 401) {
      // Token expired or invalid, sign out locally
      const errText = await res.text().catch(() => "Unknown error");
      alert("Auth Error from backend: " + errText);
      await auth.signOut();
      window.location.href = "/signin";
    }
    const errText = await res.text().catch(() => "Unknown error");
    throw new Error(`API error: ${res.status} ${errText}`);
  }
  
  // DELETE requests might return 204 No Content
  if (res.status === 204) return null;
  
  return res.json();
}

export const api = {
  bootstrap: (name: string, customToken?: string) => 
    fetchWithAuth("/companies/bootstrap", { 
      method: "POST", 
      headers: { "Content-Type": "application/json" }, 
      body: JSON.stringify({ name }),
      customToken
    }),
    
  getProducts: () => 
    fetchWithAuth("/products"),
    
  createProduct: (name: string) => 
    fetchWithAuth("/products", { 
      method: "POST", 
      headers: { "Content-Type": "application/json" }, 
      body: JSON.stringify({ name, brand: "Brand" }) 
    }),
    
  registerDocs: (productId: string, docs: {doc_id: string, doc_type: string, storage_path: string}[], photo_path: string) => 
    fetchWithAuth(`/products/${productId}/docs`, { 
      method: "POST", 
      headers: { "Content-Type": "application/json" }, 
      body: JSON.stringify({ docs, photo_path }) 
    }),
    
  processProduct: (productId: string) => 
    fetchWithAuth(`/products/${productId}/process`, { method: "POST" }),
    
  getProductStatus: (productId: string) => 
    fetchWithAuth(`/products/${productId}/status`),
    
  getProductOutputs: (productId: string) => 
    fetchWithAuth(`/products/${productId}/outputs`),
    
  getEmbed: () => 
    fetchWithAuth("/embed"),
    
  deleteProduct: (productId: string) => 
    fetchWithAuth(`/products/${productId}`, { method: "DELETE" }),
    
  updateProduct: (productId: string, name: string) => 
    fetchWithAuth(`/products/${productId}`, { 
      method: "PUT", 
      headers: { "Content-Type": "application/json" }, 
      body: JSON.stringify({ name }) 
    }),
    
  uploadFile: (productId: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return fetchWithAuth(`/products/${productId}/upload`, {
      method: "POST",
      body: formData,
    });
  },
};
