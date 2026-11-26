import type { ActivityFile, ChatArtifact, RunDigestView, SSEEngineProgressEvent } from '@/chat/types';
import { buildChatArtifact } from '@/chat/utils/build-chat-artifact';

/**
 * What a job delivered and what it only worked with (BGUI-11), as every surface reads it: the report under its card,
 * its panel's Outputs tab and their counts. The server decides which changed file is delivered (one it published as
 * an artifact, `projection/job-files.ts`); this reads that decision and never makes its own, so the counts agree.
 */

export interface JobFiles {
  /** What it delivered, as the answer's own file cards draw them (preview, download). */
  delivered: ChatArtifact[];
  /** How many changed files it only worked with; null while not known (it still runs, or an older server). */
  working: number | null;
}

const dataOf = (f: SSEEngineProgressEvent): Record<string, unknown> => (f.data ?? {}) as Record<string, unknown>;
const text = (value: unknown): string | undefined => (typeof value === 'string' && value ? value : undefined);
const count = (value: unknown): number | undefined => (typeof value === 'number' && value > 0 ? value : undefined);
const baseName = (name: string): string => name.slice(name.lastIndexOf('/') + 1);

/**
 * The files it delivered: its artifact frames while its stream is followed (the latest version of each), then its
 * summary's, which a reload reads. A discarded draft delivered nothing.
 */
export function jobDeliverables(frames: ReadonlyArray<SSEEngineProgressEvent>, digest: RunDigestView | undefined): ChatArtifact[] {
  const list: ChatArtifact[] = [];
  const put = (artifact: ChatArtifact) => {
    const at = list.findIndex((a) => (artifact.recordId ? a.recordId === artifact.recordId : !a.recordId && a.fileName === artifact.fileName));
    if (at < 0) list.push(artifact);
    else if ((artifact.version ?? 0) >= (list[at].version ?? 0)) list[at] = { ...list[at], ...artifact };
  };
  for (const f of frames) {
    const data = dataOf(f);
    if (f.kind !== 'artifact' || !f.text || data.lifecycle === 'discarded') continue;
    const recordId = text(data.artifactId);
    put(
      buildChatArtifact({
        id: recordId ?? `job-file-${f.text}`,
        fileName: baseName(f.text),
        mimeType: text(data.mimeType),
        sizeBytes: count(data.sizeBytes),
        recordId,
        version: count(data.version),
      }),
    );
  }
  for (const a of digest?.artifacts ?? []) {
    const known = list.find((o) => (a.recordId ? o.recordId === a.recordId : o.fileName === baseName(a.name)));
    if (known) continue;
    list.push(
      buildChatArtifact({
        id: a.recordId ?? `job-file-${a.name}`,
        fileName: baseName(a.name),
        mimeType: a.mimeType,
        sizeBytes: a.sizeBytes,
        artifactType: a.artifactType,
        recordId: a.recordId,
        version: a.version,
      }),
    );
  }
  return list;
}

/** A job's files once it ended, from its summary (and its frames while they are held). */
export function jobFiles(frames: ReadonlyArray<SSEEngineProgressEvent>, digest: RunDigestView | undefined): JobFiles {
  return { delivered: jobDeliverables(frames, digest), working: digest?.files.working ?? null };
}

/** The activity view's changed files, split by the server's mark: delivered first, then the working ones. */
export function splitChangedFiles(files: ReadonlyArray<ActivityFile>): { delivered: ActivityFile[]; working: ActivityFile[] } {
  return { delivered: files.filter((f) => f.delivered === true), working: files.filter((f) => f.delivered !== true) };
}
