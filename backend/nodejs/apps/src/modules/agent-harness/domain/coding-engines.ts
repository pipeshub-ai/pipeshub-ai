import type { StoredCodingEngines } from '../ports/coding-engines';

/** The person's choice that defers to the org's default engine (the stored null). */
export const ORG_DEFAULT_CHOICE = 'org_default';
/** The person's (or nobody's) choice of no engine: code runs in the built-in sandbox. */
export const NO_ENGINE = 'none';

export type CodingEngineSource = 'session' | 'user' | 'org' | 'none';

/**
 * The default chat's coding engine for one turn (ENGPICK, SLASH3): this
 * chat's choice (`/engine`), else the person's, else the org's default, else
 * none. A choice is a setting, not a fallback: a chosen engine the org no
 * longer allows gives no engine, it does not fall through to the next layer.
 */
export function resolveCodingEngine(stored: StoredCodingEngines): {
  engine: string | null;
  source: CodingEngineSource;
} {
  const session = stored.session ?? null;
  if (session === NO_ENGINE) return { engine: null, source: 'session' };
  if (session !== null) return { engine: session, source: 'session' };
  if (stored.user === NO_ENGINE) return { engine: null, source: 'user' };
  if (stored.user !== null) return { engine: stored.user, source: 'user' };
  if (stored.org !== null) return { engine: stored.org, source: 'org' };
  return { engine: null, source: 'none' };
}

/** An engine an agent's narrowing names, without its version (`codex@1.2` → `codex`). */
export const engineKindOf = (engine: string): string =>
  engine.split('@')[0] ?? engine;

/** The engines an agent's engine profile lists (`codex@1.2`, `opencode`); none for an agent without one. */
export function listedEngines(profile: unknown): string[] {
  const engines =
    profile !== null && typeof profile === 'object'
      ? (profile as { engines?: unknown }).engines
      : undefined;
  return Array.isArray(engines)
    ? engines.filter((engine): engine is string => typeof engine === 'string')
    : [];
}

/** `kind@version` as an engine, or null when it does not name a version. */
export function parseEngineRef(
  ref: string,
): { kind: string; version: string } | null {
  const at = ref.indexOf('@');
  return at > 0 && at < ref.length - 1
    ? { kind: ref.slice(0, at), version: ref.slice(at + 1) }
    : null;
}

/**
 * An engine narrowing held to the version a chat's engine turns run on (C14.01, `kind@version`), or null
 * when that version is not one the narrowing admits: the chat moved to another engine, or the agent's
 * profile no longer lists it. A narrowing that lists no engines admits any.
 */
export function pinnedEngineLayer(
  layer: unknown,
  pin: string,
): Record<string, unknown> | null {
  const engine = parseEngineRef(pin);
  if (engine === null) return null;
  const listed = listedEngines(layer);
  if (
    listed.length > 0 &&
    !listed.includes(engine.kind) &&
    !listed.includes(pin)
  ) {
    return null;
  }
  const base =
    layer !== null && typeof layer === 'object'
      ? (layer as Record<string, unknown>)
      : {};
  return { ...base, engines: [pin] };
}
