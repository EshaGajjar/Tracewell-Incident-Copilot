import { initializeApp } from "firebase/app";
import { getFirestore } from "firebase/firestore";
import { getStorage } from "firebase/storage";

const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
};

const missingFirebaseSettings = Object.entries(firebaseConfig)
  .filter(([, value]) => !value)
  .map(([key]) => key);

if (missingFirebaseSettings.length) {
  console.warn(
    `[Tracewell] Missing Firebase environment settings: ${missingFirebaseSettings.join(", ")}. ` +
    "Copy frontend/.env.example to frontend/.env and fill in the Firebase values."
  );
}

const app = initializeApp(firebaseConfig);
export const db = getFirestore(app);
export const storage = getStorage(app);

// Vite env values must be plain URLs, e.g. http://localhost:8000 (not Markdown link syntax).
export const API_URL = (import.meta.env.VITE_API_URL || "").trim().replace(/\/+$/, "");

export async function request(path, { method = "GET", body, timeoutMs = 30000 } = {}) {
  if (!API_URL) throw new Error("Backend URL is not configured. Set VITE_API_URL in frontend/.env and restart Vite.");
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_URL}${path}`, {
      method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
    const responseText = await response.text();
    let data;
    try { data = responseText ? JSON.parse(responseText) : {}; }
    catch { data = { detail: responseText }; }
    if (!response.ok) throw new Error(`${path} failed (${response.status}): ${data?.detail || data?.message || response.statusText}`);
    return data;
  } catch (error) {
    if (error?.name === "AbortError") throw new Error(`${path} timed out after ${Math.round(timeoutMs / 1000)} seconds. Check the backend terminal.`);
    if (error instanceof TypeError) throw new Error(`Could not reach Tracewell API at ${API_URL}. Check VITE_API_URL, backend status, and CORS settings.`);
    throw error;
  } finally { clearTimeout(timeoutId); }
}

export async function api(path, body, options = {}) {
  return request(path, { ...options, method: "POST", body });
}
