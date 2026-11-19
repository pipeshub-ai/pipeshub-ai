/**
 * The collection and record operations behind OpenAI files and vector stores.
 * Every call goes through the knowledge base routes with the caller's token.
 */
import { ChatRunError } from './chat-run';
import { asString, isJson, Json } from './openai-format';
import { ApiCaller, apiJson, callApi, failUnlessOk } from './pipeshub-api';
import { parseJson, readSSE } from './chat-run';

/** Where files uploaded through `POST /files` live until a vector store copies them. */
export const FILES_COLLECTION_NAME = 'OpenAI API files';

// shortcut: a listing reads at most this many records/collections; page with `after` beyond it once callers need more.
const LIST_CAP = 1000;
const PAGE = 100;

export interface Collection {
  id: string;
  name: string;
  createdAt: number;
  updatedAt: number;
}

export interface FileInfo {
  recordId: string;
  filename: string;
  bytes: number;
  createdAt: number;
  /** PipesHub indexing status: COMPLETED, FAILED, QUEUED, IN_PROGRESS, ... */
  indexingStatus: string;
  kbId: string;
}

const num = (value: unknown): number =>
  typeof value === 'number' && Number.isFinite(value) ? value : 0;

function toCollection(kb: Json): Collection {
  return {
    id: asString(kb.id),
    name: asString(kb.name),
    createdAt: num(kb.createdAtTimestamp),
    updatedAt: num(kb.updatedAtTimestamp) || num(kb.createdAtTimestamp),
  };
}

/** The record name, with its extension when the name does not already end in it. */
function filenameOf(name: string, extension: string): string {
  if (
    extension === '' ||
    name.toLowerCase().endsWith(`.${extension.toLowerCase()}`)
  ) {
    return name;
  }
  return `${name}.${extension}`;
}

// The record route carries a MIME type but no extension; listings carry the extension.
const EXTENSION_BY_MIME: Record<string, string> = {
  'application/pdf': 'pdf',
  'text/plain': 'txt',
  'text/markdown': 'md',
  'text/csv': 'csv',
  'text/html': 'html',
  'application/json': 'json',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
    'docx',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation':
    'pptx',
};

export async function listCollections(
  caller: ApiCaller,
  search?: string,
  permissions?: string,
): Promise<Collection[]> {
  const out: Collection[] = [];
  for (let page = 1; out.length < LIST_CAP; page++) {
    const body = await apiJson(
      caller,
      '/knowledgeBase',
      {
        query: {
          page,
          limit: PAGE,
          search,
          permissions,
          sortBy: 'createdAtTimestamp',
          sortOrder: 'desc',
        },
      },
      'Listing collections',
    );
    const kbs =
      isJson(body) && Array.isArray(body.knowledgeBases)
        ? body.knowledgeBases.filter(isJson)
        : [];
    out.push(...kbs.map(toCollection).filter((c) => c.id !== ''));
    const pagination =
      isJson(body) && isJson(body.pagination) ? body.pagination : {};
    if (pagination.hasNext !== true) break;
  }
  return out;
}

export async function getCollection(
  caller: ApiCaller,
  kbId: string,
): Promise<Collection> {
  const body = await apiJson(
    caller,
    `/knowledgeBase/${encodeURIComponent(kbId)}`,
    {},
    'Reading the vector store',
  );
  const kb =
    isJson(body) && isJson(body.knowledgeBase) ? body.knowledgeBase : body;
  if (!isJson(kb) || asString(kb.id) === '') {
    throw new ChatRunError('No such vector store', 404, 'not_found');
  }
  return toCollection(kb);
}

export async function createCollection(
  caller: ApiCaller,
  name: string,
): Promise<Collection> {
  const body = await apiJson(
    caller,
    '/knowledgeBase',
    { method: 'POST', json: { kbName: name } },
    'Creating the vector store',
  );
  const kb =
    isJson(body) && isJson(body.knowledgeBase) ? body.knowledgeBase : body;
  if (!isJson(kb) || asString(kb.id) === '') {
    throw new ChatRunError(
      'The collection was not created',
      502,
      'server_error',
    );
  }
  return toCollection(kb);
}

export async function renameCollection(
  caller: ApiCaller,
  kbId: string,
  name: string,
): Promise<void> {
  await apiJson(
    caller,
    `/knowledgeBase/${encodeURIComponent(kbId)}`,
    { method: 'PUT', json: { kbName: name } },
    'Renaming the vector store',
  );
}

export async function deleteCollection(
  caller: ApiCaller,
  kbId: string,
): Promise<void> {
  await apiJson(
    caller,
    `/knowledgeBase/${encodeURIComponent(kbId)}`,
    { method: 'DELETE' },
    'Deleting the vector store',
  );
}

/** Every file record in a collection, folders included, newest first. */
export async function listCollectionFiles(
  caller: ApiCaller,
  kbId: string,
): Promise<FileInfo[]> {
  const out: FileInfo[] = [];
  let cursor: string | undefined;
  while (out.length < LIST_CAP) {
    const body = await apiJson(
      caller,
      `/knowledgeBase/knowledge-hub/nodes/app/${encodeURIComponent(kbId)}`,
      {
        query: {
          flattened: true,
          nodeTypes: 'record',
          limit: PAGE,
          sortBy: 'createdAt',
          sortOrder: 'desc',
          cursor,
        },
      },
      'Listing vector store files',
    );
    const items =
      isJson(body) && Array.isArray(body.items)
        ? body.items.filter(isJson)
        : [];
    for (const item of items) {
      if (item.nodeType !== 'record' || item.recordType === 'FOLDER') continue;
      out.push({
        recordId: asString(item.id),
        filename: filenameOf(asString(item.name), asString(item.extension)),
        bytes: num(item.sizeInBytes),
        createdAt: num(item.createdAt),
        indexingStatus: asString(item.indexingStatus),
        kbId,
      });
    }
    const pagination =
      isJson(body) && isJson(body.pagination) ? body.pagination : {};
    cursor = asString(pagination.nextCursor) || undefined;
    if (pagination.hasNext !== true || cursor === undefined) break;
  }
  return out;
}

export async function getFile(
  caller: ApiCaller,
  recordId: string,
): Promise<FileInfo> {
  const body = await apiJson(
    caller,
    `/knowledgeBase/record/${encodeURIComponent(recordId)}`,
    {},
    'Reading the file',
  );
  const record = isJson(body) && isJson(body.record) ? body.record : null;
  if (record === null) throw new ChatRunError('No such file', 404, 'not_found');
  const kb =
    isJson(body) && isJson(body.knowledgeBase) ? body.knowledgeBase : {};
  return {
    recordId,
    filename: filenameOf(
      asString(record.recordName),
      EXTENSION_BY_MIME[asString(record.mimeType)] ?? '',
    ),
    bytes: num(record.sizeInBytes),
    createdAt: num(record.createdAtTimestamp),
    indexingStatus: asString(record.indexingStatus),
    kbId: asString(kb.id) || asString(record.connectorId),
  };
}

/** Uploads one file into a collection's root and returns its record id. */
export async function uploadFile(
  caller: ApiCaller,
  kbId: string,
  filename: string,
  bytes: Uint8Array,
  mimeType: string,
): Promise<string> {
  const form = new FormData();
  form.append(
    'files',
    new Blob([bytes], { type: mimeType || 'application/octet-stream' }),
    filename,
  );
  form.append(
    'files_metadata',
    JSON.stringify([{ file_path: filename, last_modified: Date.now() }]),
  );
  const response = await callApi(
    caller,
    `/knowledgeBase/${encodeURIComponent(kbId)}/upload`,
    {
      method: 'POST',
      form,
      accept: 'text/event-stream, application/json',
    },
  );
  await failUnlessOk(response, 'Uploading the file');
  if (response.body === null) {
    throw new ChatRunError(
      'The upload returned no result',
      502,
      'server_error',
    );
  }
  // The upload route streams one event per file, then `done`.
  for await (const frame of readSSE(
    response.body as unknown as AsyncIterable<Uint8Array>,
  )) {
    const data = parseJson(frame.data);
    if (data === null) continue;
    if (
      frame.event === 'file:succeeded' &&
      isJson(data) &&
      asString(data.recordId) !== ''
    ) {
      return asString(data.recordId);
    }
    if (frame.event === 'file:failed') {
      const message = isJson(data)
        ? asString(data.error) || asString(data.message)
        : '';
      throw new ChatRunError(
        message || 'The upload failed',
        400,
        'invalid_request',
      );
    }
  }
  throw new ChatRunError('The upload returned no file', 502, 'server_error');
}

export async function downloadFile(
  caller: ApiCaller,
  recordId: string,
): Promise<Response> {
  const response = await callApi(
    caller,
    `/knowledgeBase/stream/record/${encodeURIComponent(recordId)}`,
    { accept: '*/*' },
  );
  await failUnlessOk(response, 'Downloading the file');
  return response;
}

export async function deleteFile(
  caller: ApiCaller,
  recordId: string,
): Promise<void> {
  await apiJson(
    caller,
    `/knowledgeBase/record/${encodeURIComponent(recordId)}`,
    { method: 'DELETE' },
    'Deleting the file',
  );
}

/** The caller's own files collection, created on first use. */
export async function filesCollection(
  caller: ApiCaller,
  create: boolean,
): Promise<Collection | null> {
  // shortcut: two first uploads racing can each create one; the oldest is used afterwards.
  // Owned only: a collection someone shared under the same name is not the caller's.
  const found = (await listCollections(caller, FILES_COLLECTION_NAME, 'OWNER'))
    .filter((c) => c.name === FILES_COLLECTION_NAME)
    .sort((a, b) => a.createdAt - b.createdAt)[0];
  if (found !== undefined) return found;
  return create ? createCollection(caller, FILES_COLLECTION_NAME) : null;
}
