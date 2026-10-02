import { getStore } from "@netlify/blobs";

/** Minimal versioned key-value store. Blobs in production, memory in tests and local dev. */
export interface KeyValueStore {
  get(key: string): Promise<{ value: unknown; etag: string } | null>;
  /**
   * expect: undefined = unconditional write; null = only if the key does not exist;
   * string = only if the current etag matches. Returns false when the condition failed.
   */
  put(key: string, value: unknown, expect?: string | null): Promise<boolean>;
}

export class MemoryKv implements KeyValueStore {
  private data = new Map<string, { value: unknown; etag: string }>();
  private counter = 0;

  async get(key: string) {
    const hit = this.data.get(key);
    return hit ? { value: structuredClone(hit.value), etag: hit.etag } : null;
  }

  async put(key: string, value: unknown, expect?: string | null) {
    const cur = this.data.get(key);
    if (expect === null && cur) return false;
    if (typeof expect === "string" && cur?.etag !== expect) return false;
    this.data.set(key, { value: structuredClone(value), etag: `m${++this.counter}` });
    return true;
  }
}

const memoryStores = new Map<string, MemoryKv>();

export function memoryKv(name: string): MemoryKv {
  let kv = memoryStores.get(name);
  if (!kv) memoryStores.set(name, (kv = new MemoryKv()));
  return kv;
}

export function resetMemoryStores(): void {
  memoryStores.clear();
}

/** Site-wide Netlify Blobs store with strong consistency. Survives code redeploys. */
export function blobsKv(name: string): KeyValueStore {
  const store = getStore(name, { consistency: "strong" });
  return {
    async get(key) {
      const hit = await store.getWithMetadata(key, { type: "json", consistency: "strong" });
      // Blobs returns an ETag for stored entries; the type marks it optional. An empty etag means
      // "unknown", which downgrades the write below to the read-then-compare check in SnapshotStore.
      return hit ? { value: hit.data, etag: hit.etag ?? "" } : null;
    },
    async put(key, value, expect) {
      const options =
        expect === null ? { onlyIfNew: true } : typeof expect === "string" && expect !== "" ? { onlyIfMatch: expect } : {};
      const result = await store.setJSON(key, value, options);
      return result.modified;
    },
  };
}

/** True when running inside the Netlify Functions runtime (deployed or `netlify dev`). */
export function netlifyRuntimePresent(): boolean {
  return (globalThis as { Netlify?: unknown }).Netlify !== undefined || Boolean(process.env.NETLIFY_BLOBS_CONTEXT);
}

export function openKv(name: string): KeyValueStore {
  return netlifyRuntimePresent() ? blobsKv(name) : memoryKv(name);
}
