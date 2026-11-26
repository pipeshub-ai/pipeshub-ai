/**
 * Which artifacts a reader who does not own a conversation may see (SS0,
 * ARCHITECTURE 4.9). An artifact is shown only when the authority found every
 * source in its provenance readable by the viewer; anything else, including
 * an artifact it was never asked about, is hidden. A hidden artifact leaves a
 * neutral placeholder that names nothing about it: `{unavailable: true}` for a
 * live card, `::artifact_unavailable` for a marker in the answer text.
 * Markers without a record id and `::download_conversation_task` links carry
 * no lineage at all and are always hidden.
 *
 * Pure: the same input and decisions give the same output, which keeps the
 * shared stream resumable from any id.
 */

type Json = Record<string, unknown>;

export const UNAVAILABLE_ARTIFACT_MARKER = '::artifact_unavailable';

// One pass, longest form first, so a kept marker is never matched again by a shorter form.
// Groups: 1 = meta of `::artifact[name](url){meta}`, 2 = meta of `::artifact[name]{meta}`.
const MARKER =
  /::artifact\[[^\]]+\](?:\([^)]+\)\{([^}]*)\}|\{([^}]*)\}|(?:\([^)]*\))?)|::download_conversation_task\[[^\]]+\]\([^)]+\)/g;

const isObject = (value: unknown): value is Json =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

const text = (value: unknown): string | undefined =>
  typeof value === 'string' && value.length > 0 ? value : undefined;

/** `{mime|documentId|recordId|artifactType|version}`: the record id is the artifact id. */
const markerArtifactId = (meta: string): string | undefined =>
  text(meta.split('|')[2]?.trim());

/** The artifact a live card (`/artifacts/-` value) names. */
export const cardArtifactId = (card: unknown): string | undefined =>
  isObject(card)
    ? (text(card['artifactId']) ?? text(card['recordId']))
    : undefined;

/** Artifact ids named by the markers in `content`. */
export function markerArtifactIds(content: unknown): string[] {
  if (typeof content !== 'string' || !content.includes('::artifact[')) {
    return [];
  }
  const ids: string[] = [];
  for (const match of content.matchAll(MARKER)) {
    const id = markerArtifactId(match[1] ?? match[2] ?? '');
    if (id !== undefined) ids.push(id);
  }
  return ids;
}

/** An artifact version a marker shows; `version` null when the marker predates versions. */
export interface MarkerArtifactRef {
  artifactId: string;
  version: number | null;
}

const markerVersion = (meta: string): number | null => {
  const raw = meta.split('|')[4]?.trim() ?? '';
  return /^\d+$/.test(raw) ? Number(raw) : null;
};

export const artifactVersionKey = (ref: MarkerArtifactRef): string =>
  `${ref.artifactId}@${ref.version === null ? '' : String(ref.version)}`;

/** The artifact versions the markers in `content` show, first seen first, once each. */
export function markerArtifactRefs(content: unknown): MarkerArtifactRef[] {
  if (typeof content !== 'string' || !content.includes('::artifact[')) {
    return [];
  }
  const refs = new Map<string, MarkerArtifactRef>();
  for (const match of content.matchAll(MARKER)) {
    const meta = match[1] ?? match[2];
    const artifactId = meta === undefined ? undefined : markerArtifactId(meta);
    if (meta === undefined || artifactId === undefined) continue;
    const ref = { artifactId, version: markerVersion(meta) };
    refs.set(artifactVersionKey(ref), ref);
  }
  return [...refs.values()];
}

/** Where a copied artifact version now lives (SS3). */
export interface ArtifactCopyRef {
  artifactId: string;
  documentId: string;
  version: number;
}

/**
 * Answer text whose markers point at copies (SS3): a marker of a copied
 * version names the copy (its record, document and version, and a `record:`
 * link); every other marker, and every download link, becomes the neutral
 * placeholder, so nothing in a copy reaches what it was copied from.
 */
export function rewriteArtifactMarkers(
  content: string,
  copies: ReadonlyMap<string, ArtifactCopyRef>,
): string {
  if (!content.includes('::')) return content;
  return content.replace(
    MARKER,
    (marker: string, full?: string, noUrl?: string): string => {
      const meta = full ?? noUrl;
      const artifactId =
        meta === undefined ? undefined : markerArtifactId(meta);
      if (meta === undefined || artifactId === undefined) {
        return UNAVAILABLE_ARTIFACT_MARKER;
      }
      const copy = copies.get(
        artifactVersionKey({ artifactId, version: markerVersion(meta) }),
      );
      if (copy === undefined) return UNAVAILABLE_ARTIFACT_MARKER;
      const name = /^::artifact\[([^\]]+)\]/.exec(marker)?.[1] ?? '';
      const [mime = '', , , type = ''] = meta.split('|');
      return `::artifact[${name}](record:${copy.artifactId}){${mime}|${copy.documentId}|${copy.artifactId}|${type}|${String(copy.version)}}`;
    },
  );
}

/** Every artifact a conversation's messages name: in answer text, text parts and tool parts. */
export function messageArtifactIds(messages: unknown): string[] {
  if (!Array.isArray(messages)) return [];
  const ids: string[] = [];
  for (const message of messages as unknown[]) {
    if (!isObject(message)) continue;
    ids.push(...markerArtifactIds(message['content']));
    ids.push(...partArtifactIds(message['parts']));
  }
  return ids;
}

function partArtifactIds(parts: unknown): string[] {
  if (!Array.isArray(parts)) return [];
  const ids: string[] = [];
  for (const part of parts as unknown[]) {
    if (!isObject(part)) continue;
    ids.push(...markerArtifactIds(part['content']));
    const id = text(part['artifactId']);
    if (id !== undefined) ids.push(id);
    ids.push(...partArtifactIds(part['parts']));
  }
  return ids;
}

export class ArtifactAccess {
  private constructor(private readonly shown: ReadonlySet<string>) {}

  /** The artifact ids the viewer may see; every other artifact is hidden. */
  static showing(artifactIds: Iterable<string>): ArtifactAccess {
    return new ArtifactAccess(new Set(artifactIds));
  }

  /** Fail closed: every artifact hidden. */
  static hideAll(): ArtifactAccess {
    return new ArtifactAccess(new Set());
  }

  /** This access with `other`'s shown artifacts added (decisions made in batches). */
  with(other: ArtifactAccess): ArtifactAccess {
    return new ArtifactAccess(new Set([...this.shown, ...other.shown]));
  }

  shows(artifactId: string | undefined): boolean {
    return artifactId !== undefined && this.shown.has(artifactId);
  }

  /** A live artifact card (`STATE_DELTA add /artifacts/-`). */
  card(card: unknown): unknown {
    return this.shows(cardArtifactId(card)) ? card : { unavailable: true };
  }

  /** Answer text with every marker of a hidden artifact replaced by the placeholder. */
  text(content: string): string {
    if (!content.includes('::')) return content;
    return content.replace(
      MARKER,
      (marker: string, full?: string, noUrl?: string): string => {
        const meta = full ?? noUrl;
        return meta !== undefined && this.shows(markerArtifactId(meta))
          ? marker
          : UNAVAILABLE_ARTIFACT_MARKER;
      },
    );
  }

  /** Conversation messages: answer text, and each part's text and artifact reference. */
  messages<T>(messages: T): T {
    if (!Array.isArray(messages)) return messages;
    return messages.map((message: unknown) =>
      isObject(message) ? this.part(message) : message,
    ) as T;
  }

  /** A finished answer (the final run-state snapshot). */
  completion(completion: Json): Json {
    return this.part(completion, 'answer');
  }

  private part(value: Json, textField = 'content'): Json {
    const out: Json = { ...value };
    if (typeof out[textField] === 'string') {
      out[textField] = this.text(out[textField]);
    }
    if (textField !== 'content' && typeof out['content'] === 'string') {
      out['content'] = this.text(out['content']);
    }
    const artifactId = text(out['artifactId']);
    if (artifactId !== undefined && !this.shows(artifactId)) {
      delete out['artifactId'];
    }
    if (Array.isArray(out['parts'])) {
      out['parts'] = (out['parts'] as unknown[])
        .filter((part) => !isObject(part) || !ownerOnly(part))
        .map((part) => (isObject(part) ? this.part(part) : part));
    }
    return out;
  }
}

/**
 * An engine's commands, output and diffs and the approvals asked of the
 * owner reach no other audience live (U1 FB-2, E1 PR-4); a saved
 * conversation keeps that rule (U3).
 */
const OWNER_ONLY_PARTS: ReadonlySet<unknown> = new Set([
  'engine_task',
  'approval',
]);

/**
 * Notices about the owner's own connections name their servers and toolsets, and say what the owner should sign in
 * to: only the person whose run it is reads them, as the live stream already keeps them (MCPNOTE-1).
 */
const OWNER_ONLY_NOTICES: ReadonlySet<unknown> = new Set([
  'mcp_unavailable',
  'mcp_tool_held',
  'credential_missing',
]);

const ownerOnly = (part: Json): boolean =>
  OWNER_ONLY_PARTS.has(part['type']) ||
  (part['type'] === 'notice' && OWNER_ONLY_NOTICES.has(part['code']));
