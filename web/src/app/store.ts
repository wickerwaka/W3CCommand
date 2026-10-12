// Saved games in IndexedDB. A save is the whole 512 KB RAM image (the simulation state is the
// game's own memory, so restoring is just loading it again). One autosave slot, a few manual
// slots, and the last state loaded from a file. Everything works without storage (private
// windows, blocked storage): saving then reports failure and loading finds nothing.
const DB = "w3ccommand", STORE = "states";

export const SLOTS = ["auto", "1", "2", "3", "4"] as const;
export type Slot = (typeof SLOTS)[number] | "last";

export interface StoredState {
  name: string;
  ram: Uint8Array;
  savedAt?: number;
}

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function tx<T>(mode: IDBTransactionMode, run: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await open();
  try {
    return await new Promise<T>((resolve, reject) => {
      const req = run(db.transaction(STORE, mode).objectStore(STORE));
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  } finally {
    db.close();
  }
}

const key = (slot: Slot) => (slot === "last" ? "last" : `slot:${slot}`);

export async function saveState(slot: Slot, state: StoredState): Promise<boolean> {
  try {
    await tx("readwrite", (s) => s.put({ name: state.name, ram: state.ram.slice(), savedAt: Date.now() }, key(slot)));
    return true;
  } catch {
    return false;
  }
}

export async function loadState(slot: Slot): Promise<StoredState | null> {
  try {
    const v = (await tx("readonly", (s) => s.get(key(slot)))) as StoredState | undefined;
    return v ? { name: v.name, ram: new Uint8Array(v.ram), savedAt: v.savedAt } : null;
  } catch {
    return null;
  }
}

/** What each slot holds (name and time), without the images. */
export async function listSaves(): Promise<Map<Slot, { name: string; savedAt?: number }>> {
  const out = new Map<Slot, { name: string; savedAt?: number }>();
  for (const slot of [...SLOTS, "last"] as Slot[]) {
    const s = await loadState(slot);
    if (s) out.set(slot, { name: s.name, savedAt: s.savedAt });
  }
  return out;
}
