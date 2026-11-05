const ANSI = /\u001b\[[0-9;?]*[ -/]*[@-~]/g;

/** Recorded logs are untrusted. Strip terminal controls; leave markup as text. */
export function visibleText(value: unknown): string {
  return String(value ?? "").replace(ANSI, "");
}

export function splitHighlight(
  value: string,
  query: string,
): { text: string; hit: boolean }[] {
  const clean = visibleText(value);
  const needle = query.trim().toLowerCase();
  if (!needle) return [{ text: clean, hit: false }];
  const lower = clean.toLowerCase();
  const parts: { text: string; hit: boolean }[] = [];
  let cursor = 0;
  while (cursor < clean.length) {
    const at = lower.indexOf(needle, cursor);
    if (at < 0) {
      parts.push({ text: clean.slice(cursor), hit: false });
      break;
    }
    if (at > cursor) parts.push({ text: clean.slice(cursor, at), hit: false });
    parts.push({ text: clean.slice(at, at + needle.length), hit: true });
    cursor = at + needle.length;
  }
  return parts.filter((part) => part.text.length > 0);
}

export function matchesQuery(value: string, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return visibleText(value).toLowerCase().includes(needle);
}
