/** Null-tolerant reads for API fields that the types declare required but a response may omit. */
export function asArray<T>(v: T[] | null | undefined): T[] {
  return Array.isArray(v) ? v : [];
}

export function asRecord<V>(v: Record<string, V> | null | undefined): Record<string, V> {
  return v && typeof v === 'object' ? v : {};
}

export function lower(v: string | null | undefined): string {
  return typeof v === 'string' ? v.toLowerCase() : '';
}
