/**
 * OpenAI Files and Vector Stores object shapes, built from PipesHub records
 * and collections. Pure, so every shape is unit-tested.
 */
import { Collection, FileInfo } from './collections';
import { isJson, Json, toFileId, toVectorStoreId } from './openai-format';

const seconds = (ms: number): number => Math.floor(ms / 1000);

/** OpenAI file status from the record's indexing status. */
export function fileStatus(
  indexingStatus: string,
): 'uploaded' | 'processed' | 'error' {
  if (indexingStatus === 'COMPLETED') return 'processed';
  if (
    indexingStatus === 'FAILED' ||
    indexingStatus === 'FILE_TYPE_NOT_SUPPORTED'
  ) {
    return 'error';
  }
  return 'uploaded';
}

export function vectorStoreFileStatus(
  indexingStatus: string,
): 'in_progress' | 'completed' | 'failed' {
  const status = fileStatus(indexingStatus);
  if (status === 'processed') return 'completed';
  return status === 'error' ? 'failed' : 'in_progress';
}

export function fileObject(file: FileInfo, purpose = 'user_data'): Json {
  return {
    id: toFileId(file.recordId),
    object: 'file',
    bytes: file.bytes,
    created_at: seconds(file.createdAt),
    expires_at: null,
    filename: file.filename,
    purpose,
    status: fileStatus(file.indexingStatus),
    status_details:
      fileStatus(file.indexingStatus) === 'error' ? file.indexingStatus : null,
  };
}

export interface FileCounts {
  in_progress: number;
  completed: number;
  failed: number;
  cancelled: number;
  total: number;
}

export function fileCounts(files: FileInfo[]): FileCounts {
  const counts: FileCounts = {
    in_progress: 0,
    completed: 0,
    failed: 0,
    cancelled: 0,
    total: 0,
  };
  for (const file of files) {
    counts[vectorStoreFileStatus(file.indexingStatus)] += 1;
    counts.total += 1;
  }
  return counts;
}

export function vectorStoreObject(kb: Collection, files: FileInfo[]): Json {
  const counts = fileCounts(files);
  return {
    id: toVectorStoreId(kb.id),
    object: 'vector_store',
    created_at: seconds(kb.createdAt),
    name: kb.name,
    usage_bytes: files.reduce((sum, f) => sum + f.bytes, 0),
    file_counts: counts,
    status: counts.in_progress > 0 ? 'in_progress' : 'completed',
    expires_after: null,
    expires_at: null,
    last_active_at: seconds(kb.updatedAt),
    metadata: {},
  };
}

export function vectorStoreFileObject(
  vectorStoreId: string,
  file: FileInfo,
  id = toFileId(file.recordId),
): Json {
  const status = vectorStoreFileStatus(file.indexingStatus);
  return {
    id,
    object: 'vector_store.file',
    usage_bytes: file.bytes,
    created_at: seconds(file.createdAt),
    vector_store_id: vectorStoreId,
    status,
    last_error:
      status === 'failed'
        ? {
            code: 'unsupported_file',
            message: `Indexing ended as ${file.indexingStatus}`,
          }
        : null,
    chunking_strategy: { type: 'other' },
    attributes: {},
  };
}

export interface ListParams {
  limit: number;
  after?: string;
  before?: string;
  order: 'asc' | 'desc';
}

/** OpenAI list paging over already-sorted (newest first) items with an `id`. */
export function listPage(items: Json[], params: ListParams): Json {
  const ordered = params.order === 'asc' ? [...items].reverse() : items;
  let start = 0;
  let end = ordered.length;
  if (params.after !== undefined) {
    const at = ordered.findIndex((item) => item.id === params.after);
    start = at >= 0 ? at + 1 : ordered.length;
  }
  if (params.before !== undefined) {
    const at = ordered.findIndex((item) => item.id === params.before);
    end = at >= 0 ? at : 0;
  }
  const window = ordered.slice(start, end);
  const data = window.slice(0, params.limit);
  return {
    object: 'list',
    data,
    first_id: data[0]?.id ?? null,
    last_id: data[data.length - 1]?.id ?? null,
    has_more: window.length > data.length,
  };
}

/** Reads OpenAI's `limit`, `after`, `before` and `order` query parameters. */
export function listParams(
  query: unknown,
  defaultLimit: number,
  maxLimit: number,
): ListParams {
  const q = isJson(query) ? query : {};
  const raw = Number(q.limit);
  const limit =
    Number.isInteger(raw) && raw > 0 ? Math.min(raw, maxLimit) : defaultLimit;
  const str = (value: unknown): string | undefined =>
    typeof value === 'string' && value !== '' ? value : undefined;
  return {
    limit,
    after: str(q.after),
    before: str(q.before),
    order: q.order === 'asc' ? 'asc' : 'desc',
  };
}

/** One retrieval chunk as an OpenAI vector store search result. */
export function searchResult(
  chunk: Json,
  fileIdFor: (recordId: string) => string,
): Json | null {
  const record = isJson(chunk.record) ? chunk.record : {};
  const recordId = typeof record.recordId === 'string' ? record.recordId : '';
  if (recordId === '') return null;
  return {
    file_id: fileIdFor(recordId),
    filename: typeof record.name === 'string' ? record.name : '',
    score: typeof chunk.score === 'number' ? chunk.score : 0,
    attributes: {},
    content: [
      { type: 'text', text: typeof chunk.text === 'string' ? chunk.text : '' },
    ],
  };
}
