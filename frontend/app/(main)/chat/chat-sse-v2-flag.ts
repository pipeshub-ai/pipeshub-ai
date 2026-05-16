/**
 * Opt-in SSE v2 (extra wire events + trace UI). Next.js inlines
 * `process.env.NEXT_PUBLIC_*` at build time — keep a direct property read here.
 */
const RAW =
  typeof process !== 'undefined' ? process.env.NEXT_PUBLIC_CHAT_SSE_V2 : undefined;
const NORM = (RAW ?? '').trim().toLowerCase();

export const CHAT_SSE_V2 =
  NORM === '1' || NORM === 'true' || NORM === 'yes' || NORM === 'on';
