import { IMessage } from '../types/conversation.interfaces';

/**
 * An artifact is reachable from any chat whose messages carry its id, so a fork
 * that kept the original's ids would read the original's later versions and
 * write its own edits into the original. On fork, the artifacts the copied
 * messages reference are copied into new ones and the ids in the copied
 * messages are pointed at the copies.
 */

export interface ArtifactRef {
  artifactId: string;
  /** Highest version any copied message shows; later versions stay behind. */
  upToVersion: number;
}

export interface ArtifactCopy {
  sourceArtifactId: string;
  sourceDocumentId?: string | null;
  artifactId: string;
  documentId?: string | null;
  versions: { from: number; to: number }[];
}

export interface ArtifactCopyFailure {
  artifactId: string;
  reason: 'not_found' | 'forbidden' | 'error';
}

export interface ArtifactCopyOutcome {
  copies: ArtifactCopy[];
  failed: ArtifactCopyFailure[];
}

/** Copies `refs` into the fork `conversationId`. */
export type ArtifactCopier = (
  conversationId: string,
  refs: ArtifactRef[],
) => Promise<ArtifactCopyOutcome>;

// Same shape the chat UI parses (`parseArtifactMarkers`); the meta segments are
// `mime|documentId|recordId|artifactType|version`.
const ARTIFACT_MARKER = /::artifact\[([^\]]+)\]\(([^)]+)\)\{([^}]*)\}/g;
const RECORD_URL_PREFIX = 'record:';
const META_DOCUMENT_ID = 1;
const META_RECORD_ID = 2;
const META_VERSION = 4;

function parseVersion(raw: string | undefined): number | undefined {
  const parsed = Number.parseInt((raw ?? '').trim(), 10);
  return Number.isNaN(parsed) || parsed < 1 ? undefined : parsed;
}

/**
 * Artifact ids named by markers in `contents`, each with the highest version
 * shown. A marker with no version predates versioning, so only version 1 existed.
 */
export function collectArtifactRefs(contents: Iterable<string>): ArtifactRef[] {
  const highest = new Map<string, number>();
  for (const content of contents) {
    for (const match of content.matchAll(ARTIFACT_MARKER)) {
      const meta = (match[3] ?? '').split('|');
      const artifactId = (meta[META_RECORD_ID] ?? '').trim();
      if (!artifactId) {
        continue;
      }
      const version = parseVersion(meta[META_VERSION]) ?? 1;
      highest.set(artifactId, Math.max(highest.get(artifactId) ?? 0, version));
    }
  }
  return [...highest].map(([artifactId, upToVersion]) => ({
    artifactId,
    upToVersion,
  }));
}

export class ArtifactCopyMap {
  private readonly bySource = new Map<string, ArtifactCopy>();

  constructor(copies: ArtifactCopy[]) {
    for (const copy of copies) {
      this.bySource.set(copy.sourceArtifactId, copy);
    }
  }

  get size(): number {
    return this.bySource.size;
  }

  get(sourceArtifactId: string): ArtifactCopy | undefined {
    return this.bySource.get(sourceArtifactId);
  }

  values(): IterableIterator<ArtifactCopy> {
    return this.bySource.values();
  }

  /** The copy's version for a source version; the newest copied one when unknown. */
  versionFor(copy: ArtifactCopy, sourceVersion: number | undefined): number {
    const newest = Math.max(1, ...copy.versions.map((v) => v.to));
    if (sourceVersion === undefined) {
      return newest;
    }
    return copy.versions.find((v) => v.from === sourceVersion)?.to ?? newest;
  }
}

function replaceAll(text: string, from: string, to: string): string {
  return from && from !== to && text.includes(from)
    ? text.split(from).join(to)
    : text;
}

function swapIds(text: string, copies: ArtifactCopyMap): string {
  let out = text;
  for (const copy of copies.values()) {
    out = replaceAll(out, copy.sourceArtifactId, copy.artifactId);
    if (copy.sourceDocumentId && copy.documentId) {
      out = replaceAll(out, copy.sourceDocumentId, copy.documentId);
    }
  }
  return out;
}

function rewriteMarkers(content: string, copies: ArtifactCopyMap): string {
  return content.replace(
    ARTIFACT_MARKER,
    (whole, fileName: string, url: string, rawMeta: string) => {
      const meta = rawMeta.split('|');
      const copy = copies.get((meta[META_RECORD_ID] ?? '').trim());
      if (!copy) {
        return whole;
      }
      meta[META_RECORD_ID] = copy.artifactId;
      if (copy.documentId && meta.length > META_DOCUMENT_ID) {
        meta[META_DOCUMENT_ID] = copy.documentId;
      }
      if (meta.length > META_VERSION) {
        meta[META_VERSION] = String(
          copies.versionFor(copy, parseVersion(meta[META_VERSION])),
        );
      }
      // A signed URL would still point at the original's document.
      const newUrl = url.startsWith(RECORD_URL_PREFIX)
        ? `${RECORD_URL_PREFIX}${copy.artifactId}`
        : url;
      return `::artifact[${fileName}](${newUrl}){${meta.join('|')}}`;
    },
  );
}

function rewriteValue(value: unknown, copies: ArtifactCopyMap): unknown {
  if (typeof value === 'string') {
    return swapIds(value, copies);
  }
  if (Array.isArray(value)) {
    return value.map((item) => rewriteValue(item, copies));
  }
  // Only plain objects: ObjectIds and Dates must survive untouched.
  if (
    value !== null &&
    typeof value === 'object' &&
    Object.getPrototypeOf(value) === Object.prototype
  ) {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        rewriteValue(item, copies),
      ]),
    );
  }
  return value;
}

/**
 * `message` with every reference to a copied artifact pointed at the copy:
 * the cards in the text, and the ids a replayed tool call shows the model.
 * The input is not mutated.
 */
export function pointMessageAtCopies(
  message: IMessage,
  copies: ArtifactCopyMap,
): IMessage {
  if (copies.size === 0) {
    return message;
  }
  const next: IMessage = { ...message };
  if (typeof message.content === 'string') {
    next.content = swapIds(rewriteMarkers(message.content, copies), copies);
  }
  if (Array.isArray(message.parts)) {
    next.parts = rewriteValue(message.parts, copies) as IMessage['parts'];
  }
  return next;
}
