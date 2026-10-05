import { ref, uploadBytes, getDownloadURL } from "firebase/storage";
import { storage } from "./firebase.js";

// Uploads files to incidents/{id}/attachments and returns [{name, url}]
export async function uploadAttachments(incidentId, files) {
  const out = [];
  for (const f of files) {
    const r = ref(storage, `incidents/${incidentId}/attachments/${Date.now()}-${f.name}`);
    await uploadBytes(r, f);
    out.push({ name: f.name, url: await getDownloadURL(r) });
  }
  return out;
}
