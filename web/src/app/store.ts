// Keeps the last loaded state in IndexedDB, so a reload continues where it was. A convenience:
// everything works without it (private windows, blocked storage).
const DB = "w3ccommand", STORE = "states", KEY = "last";

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export interface StoredState {
  name: string;
  ram: Uint8Array;
}

export async function saveLastState(state: StoredState): Promise<void> {
  try {
    const db = await open();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).put({ name: state.name, ram: state.ram.slice() }, KEY);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
    db.close();
  } catch {
    // storage unavailable: nothing to remember
  }
}

export async function loadLastState(): Promise<StoredState | null> {
  try {
    const db = await open();
    const value = await new Promise<StoredState | undefined>((resolve, reject) => {
      const req = db.transaction(STORE).objectStore(STORE).get(KEY);
      req.onsuccess = () => resolve(req.result as StoredState | undefined);
      req.onerror = () => reject(req.error);
    });
    db.close();
    return value ? { name: value.name, ram: new Uint8Array(value.ram) } : null;
  } catch {
    return null;
  }
}
