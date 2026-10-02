import { AdapterError } from "./adapter.ts";

export interface Page<T> {
  items: T[];
  /** null or undefined when this was the last page. */
  nextCursor?: string | null;
}

/**
 * Read every page or fail. A partial result is never returned: any page failure, a repeated
 * cursor (infinite loop), or hitting maxPages throws, so callers cannot count a truncated set.
 * Source-agnostic: the caller supplies fetchPage and decides how a cursor maps to a request.
 */
export async function collectPages<T>(
  fetchPage: (cursor: string | null) => Promise<Page<T>>,
  opts: { maxPages?: number } = {},
): Promise<T[]> {
  const maxPages = opts.maxPages ?? 200;
  const all: T[] = [];
  const seen = new Set<string>();
  let cursor: string | null = null;
  for (let pageNumber = 1; ; pageNumber++) {
    if (pageNumber > maxPages) throw new AdapterError("incomplete");
    const page: Page<T> = await fetchPage(cursor);
    if (!Array.isArray(page.items)) throw new AdapterError("malformed");
    all.push(...page.items);
    const next: string | null = page.nextCursor ?? null;
    if (next === null) return all;
    if (seen.has(next)) throw new AdapterError("malformed");
    seen.add(next);
    cursor = next;
  }
}
