import aliases from '@/chat/mentions/reserved-aliases.json';

const RESERVED: ReadonlySet<string> = new Set([...aliases.assistant, ...aliases.inert]);

/** Mirrors `^[a-z0-9-]{2,40}$` on the server (`app/modules/agents/handles.py`). */
const HANDLE_PATTERN = /^[a-z0-9-]{2,40}$/;
const MAX_BASE_LENGTH = 37;
const FALLBACK_BASE = 'new-agent';

export type AgentHandleProblem = 'invalid' | 'reserved';

export type AgentHandleServerError = {
  code: 'HANDLE_TAKEN' | 'HANDLE_RESERVED' | 'HANDLE_INVALID';
  handle: string;
  suggestion?: string;
};

/** A typed `@` is part of how people write handles, never part of the stored value. */
export function normalizeHandleInput(raw: string): string {
  return raw.trim().replace(/^@/, '');
}

export function validateAgentHandle(raw: string): AgentHandleProblem | null {
  const handle = normalizeHandleInput(raw);
  if (!HANDLE_PATTERN.test(handle)) return 'invalid';
  return RESERVED.has(handle) ? 'reserved' : null;
}

/** The handle the server derives from a name when none is given (before any `-2` suffix). */
export function slugifyAgentName(name: string): string {
  const ascii = name
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^\x00-\x7f]/g, '')
    .toLowerCase();
  let slug = ascii.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, MAX_BASE_LENGTH).replace(/-+$/g, '');
  if (!slug) return FALLBACK_BASE;
  if (slug.length < 2) slug = `${slug}-agent`;
  if (RESERVED.has(slug)) slug = `${slug}-agent`;
  return slug;
}

export function handleServerError(error: unknown, requested: string): AgentHandleServerError | null {
  const processed = error as { code?: unknown; details?: { suggestion?: unknown } } | null;
  const code = processed?.code;
  if (code !== 'HANDLE_TAKEN' && code !== 'HANDLE_RESERVED' && code !== 'HANDLE_INVALID') return null;
  const suggestion = processed?.details?.suggestion;
  return {
    code,
    handle: requested,
    suggestion: typeof suggestion === 'string' && suggestion ? suggestion : undefined,
  };
}
