import { useEffect, useState } from "react";
import { brandItems } from "./api/client.js";

// Brand menus already fetched this visit, kept in memory only so reopening a menu is instant.
// A request keeps running if its menu is collapsed; its answer only fills this cache and can never
// reopen anything. A failed request is dropped so the next open tries again.
const menus = new Map();

function loadMenu(slug, includePending) {
  const key = `${slug}\u0000${includePending}`;
  let entry = menus.get(key);
  if (!entry) {
    entry = { items: null, promise: null };
    entry.promise = brandItems(slug, { includePending }).then(response => {
      if (!Array.isArray(response.items)) throw new Error("Menu details could not be loaded.");
      entry.items = response.items;
      return response.items;
    }).catch(error => { menus.delete(key); throw error; });
    menus.set(key, entry);
  }
  return entry;
}

function cachedState(slug, includePending) {
  const items = menus.get(`${slug}\u0000${includePending}`)?.items;
  return items ? { loading: false, items, error: null } : { loading: true, items: [], error: null };
}

export function useBrandMenu(slug, includePending) {
  const [state, setState] = useState(() => cachedState(slug, includePending));
  useEffect(() => {
    let mounted = true;
    const entry = loadMenu(slug, includePending);
    if (entry.items) { setState({ loading: false, items: entry.items, error: null }); return undefined; }
    setState({ loading: true, items: [], error: null });
    entry.promise.then(
      items => { if (mounted) setState({ loading: false, items, error: null }); },
      error => { if (mounted) setState({ loading: false, items: [], error: error.message }); },
    );
    return () => { mounted = false; };
  }, [slug, includePending]);
  return state;
}

export function clearMenuCache() { menus.clear(); }
