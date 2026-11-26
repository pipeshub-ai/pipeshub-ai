import { effectiveMimeType } from '@/lib/utils/file-icon-utils';
import type { ChatArtifact } from '../types';

/** Fallback mime→artifact-type mapping, used when a producer doesn't supply
 * an explicit `artifactType` (older persisted `::artifact` markers with no
 * type segment; a malformed/incomplete SSE event). Mirrors the backend's
 * `MIME_TO_ARTIFACT_TYPE` (app/sandbox/artifact_upload.py). */
export function artifactTypeFromMime(mime: string): string {
  if (mime.startsWith('image/')) return 'IMAGE';
  if (mime === 'text/csv' || mime.includes('spreadsheetml') || mime === 'application/vnd.ms-excel')
    return 'SPREADSHEET';
  if (mime.includes('presentationml') || mime === 'application/vnd.ms-powerpoint')
    return 'PRESENTATION';
  if (
    mime === 'application/pdf' ||
    mime.includes('wordprocessingml') ||
    mime === 'text/html' ||
    mime === 'text/markdown'
  )
    return 'DOCUMENT';
  if (mime === 'application/json') return 'DATA_FILE';
  return 'OTHER';
}

/** Loosely-typed input for `buildChatArtifact` — every field is optional
 * except `fileName` so both producers (the live SSE `artifact` event in
 * `streaming.ts`, and the persisted `::artifact` marker parser in
 * `parse-download-markers.ts`) can pass through only what they actually
 * have, instead of each hand-rolling its own defaulting logic. */
export interface ChatArtifactInput {
  /** Fully resolved identity for this artifact. Callers decide their own
   * priority among whatever identifiers they have (e.g. SSE's `artifactId`
   * vs a marker's `recordId`/legacy `documentId`) — this factory only
   * supplies the LAST-RESORT random fallback when the caller has none. */
  id?: string;
  fileName: string;
  mimeType?: string;
  sizeBytes?: number;
  downloadUrl?: string;
  artifactType?: string;
  recordId?: string;
  version?: number;
  derivedFromCodeArtifactId?: string;
  visibility?: 'VISIBLE' | 'STAGING';
  /** The viewer of a shared conversation may not open it: every other field is ignored. */
  unavailable?: boolean;
}

/** Single place that turns a partial artifact description into the
 * `ChatArtifact` shape the chat UI renders — used by both the live SSE
 * `artifact` event handler (`streaming.ts::onArtifact`) and the persisted
 * `::artifact` marker parser (`parse-download-markers.ts::extractAnswerMarkers`),
 * which previously each hand-built this object with subtly different
 * defaulting rules. */
export function buildChatArtifact(input: ChatArtifactInput): ChatArtifact {
  if (input.unavailable) return unavailableArtifact(input.id);
  const mimeType = effectiveMimeType(input.fileName, input.mimeType);
  const declared = input.artifactType && input.artifactType !== 'OTHER' ? input.artifactType : '';
  return {
    id: input.id || `artifact-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    fileName: input.fileName,
    mimeType,
    sizeBytes: input.sizeBytes ?? 0,
    downloadUrl: input.downloadUrl ?? '',
    artifactType: declared || artifactTypeFromMime(mimeType),
    recordId: input.recordId || undefined,
    version: input.version,
    derivedFromCodeArtifactId: input.derivedFromCodeArtifactId,
    visibility: input.visibility,
  };
}

/** The placeholder of a file the viewer may not open: no name, type, link or record. */
export function unavailableArtifact(id?: string): ChatArtifact {
  return {
    id: id || `artifact-unavailable-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    fileName: '',
    mimeType: 'application/octet-stream',
    sizeBytes: 0,
    downloadUrl: '',
    artifactType: '',
    unavailable: true,
  };
}
