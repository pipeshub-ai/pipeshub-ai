import { Request, Response } from 'express';
import { randomBytes } from 'crypto';

import {
  Collection,
  createCollection,
  deleteCollection,
  deleteFile,
  downloadFile,
  FileInfo,
  getCollection,
  getFile,
  listCollectionFiles,
  listCollections,
  renameCollection,
  uploadFile,
} from '../utils/collections';
import {
  asString,
  isJson,
  Json,
  kbIdFromVectorStoreId,
  recordIdFromFileId,
  toFileId,
  toVectorStoreId,
} from '../utils/openai-format';
import {
  fileCounts,
  listPage,
  listParams,
  searchResult,
  vectorStoreFileObject,
  vectorStoreObject,
} from '../utils/openai-objects';
import { ApiCaller, apiJson } from '../utils/pipeshub-api';
import { callerOf } from './files.controller';
import {
  OpenAICompatDeps,
  requestBody,
  sendError,
  sendRunError,
} from './openai_compat.controller';

type Handler = (req: Request, res: Response) => Promise<void>;

// shortcut: a batch id carries its file ids, so batches need no storage; 100 files keep it URL-sized.
const MAX_BATCH_FILES = 100;

function kbIdParam(req: Request, res: Response): string | null {
  const params = req.params as Record<string, string | undefined>;
  const kbId = kbIdFromVectorStoreId(params.vectorStoreId);
  if (kbId === null) {
    sendError(
      res,
      404,
      `No vector store found with id '${params.vectorStoreId ?? ''}'.`,
      'not_found',
      'vector_store_id',
    );
  }
  return kbId;
}

async function storeObject(caller: ApiCaller, kb: Collection): Promise<Json> {
  return vectorStoreObject(kb, await listCollectionFiles(caller, kb.id));
}

/**
 * The record that holds `fileId` in this collection: the file itself when it
 * lives there, else the copy made when it was added (same name and size).
 */
async function resolveStoreFile(
  caller: ApiCaller,
  kbId: string,
  recordId: string,
  files?: FileInfo[],
): Promise<FileInfo | null> {
  const inStore = files ?? (await listCollectionFiles(caller, kbId));
  const direct = inStore.find((f) => f.recordId === recordId);
  if (direct !== undefined) return direct;
  let source: FileInfo;
  try {
    source = await getFile(caller, recordId);
  } catch {
    return null;
  }
  return (
    inStore.find(
      (f) => f.filename === source.filename && f.bytes === source.bytes,
    ) ?? null
  );
}

/**
 * Adds a file to a collection. A record belongs to one collection, so a file
 * from elsewhere (the files collection, another store) is copied in.
 */
async function addFile(
  caller: ApiCaller,
  kbId: string,
  recordId: string,
  files: FileInfo[],
): Promise<FileInfo> {
  const existing = await resolveStoreFile(caller, kbId, recordId, files);
  if (existing !== null) return existing;
  const source = await getFile(caller, recordId);
  const download = await downloadFile(caller, recordId);
  const bytes = new Uint8Array(await download.arrayBuffer());
  const copyId = await uploadFile(
    caller,
    kbId,
    source.filename,
    bytes,
    download.headers.get('content-type') ?? 'application/octet-stream',
  );
  const copy = await getFile(caller, copyId);
  files.unshift(copy);
  return copy;
}

function fileIdsOf(value: unknown): string[] | null {
  if (value === undefined) return [];
  if (!Array.isArray(value)) return null;
  const ids = value.map(recordIdFromFileId);
  return ids.every((id): id is string => id !== null) ? ids : null;
}

export const createVectorStore =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    const body = requestBody(req);
    const recordIds = fileIdsOf(body.file_ids);
    if (recordIds === null) {
      sendError(
        res,
        400,
        'file_ids must be file ids returned by POST /files',
        'invalid_request',
        'file_ids',
      );
      return;
    }
    try {
      const caller = callerOf(deps, req);
      const name =
        asString(body.name).trim() ||
        `Vector store ${new Date().toISOString().slice(0, 10)}`;
      const kb = await createCollection(caller, name);
      const files: FileInfo[] = [];
      for (const recordId of recordIds)
        await addFile(caller, kb.id, recordId, files);
      res.status(200).json(vectorStoreObject(kb, files));
    } catch (error) {
      sendRunError(res, error);
    }
  };

export const listVectorStores =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    try {
      const caller = callerOf(deps, req);
      const params = listParams(req.query, 20, 100);
      const kbs = await listCollections(caller);
      const page = listPage(
        kbs.map((kb) => ({ id: toVectorStoreId(kb.id), kb })),
        params,
      );
      const data = await Promise.all(
        (page.data as Array<{ kb: Collection }>).map((item) =>
          storeObject(caller, item.kb),
        ),
      );
      res.status(200).json({ ...page, data });
    } catch (error) {
      sendRunError(res, error);
    }
  };

export const retrieveVectorStore =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    const kbId = kbIdParam(req, res);
    if (kbId === null) return;
    try {
      const caller = callerOf(deps, req);
      res
        .status(200)
        .json(await storeObject(caller, await getCollection(caller, kbId)));
    } catch (error) {
      sendRunError(res, error);
    }
  };

export const modifyVectorStore =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    const kbId = kbIdParam(req, res);
    if (kbId === null) return;
    try {
      const caller = callerOf(deps, req);
      const name = asString(requestBody(req).name).trim();
      if (name !== '') await renameCollection(caller, kbId, name);
      res
        .status(200)
        .json(await storeObject(caller, await getCollection(caller, kbId)));
    } catch (error) {
      sendRunError(res, error);
    }
  };

export const removeVectorStore =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    const kbId = kbIdParam(req, res);
    if (kbId === null) return;
    try {
      await deleteCollection(callerOf(deps, req), kbId);
      res.status(200).json({
        id: toVectorStoreId(kbId),
        object: 'vector_store.deleted',
        deleted: true,
      });
    } catch (error) {
      sendRunError(res, error);
    }
  };

export const createVectorStoreFile =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    const kbId = kbIdParam(req, res);
    if (kbId === null) return;
    const fileId = asString(requestBody(req).file_id);
    const recordId = recordIdFromFileId(fileId);
    if (recordId === null) {
      sendError(
        res,
        400,
        'file_id must be a file id returned by POST /files',
        'invalid_request',
        'file_id',
      );
      return;
    }
    try {
      const caller = callerOf(deps, req);
      const files = await listCollectionFiles(caller, kbId);
      const file = await addFile(caller, kbId, recordId, files);
      res
        .status(200)
        .json(vectorStoreFileObject(toVectorStoreId(kbId), file, fileId));
    } catch (error) {
      sendRunError(res, error);
    }
  };

export const listVectorStoreFiles =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    const kbId = kbIdParam(req, res);
    if (kbId === null) return;
    try {
      const files = await listCollectionFiles(callerOf(deps, req), kbId);
      const filter =
        typeof req.query.filter === 'string' ? req.query.filter : '';
      const objects = files
        .map((f) => vectorStoreFileObject(toVectorStoreId(kbId), f))
        .filter((o) => filter === '' || o.status === filter);
      res.status(200).json(listPage(objects, listParams(req.query, 20, 100)));
    } catch (error) {
      sendRunError(res, error);
    }
  };

/** `:fileId` resolved to its record in the store, or null after answering 404. */
async function storeFileParam(
  deps: OpenAICompatDeps,
  req: Request,
  res: Response,
): Promise<{ kbId: string; fileId: string; file: FileInfo } | null> {
  const kbId = kbIdParam(req, res);
  if (kbId === null) return null;
  const fileId = asString(
    (req.params as Record<string, string | undefined>).fileId,
  );
  const recordId = recordIdFromFileId(fileId);
  const file =
    recordId === null
      ? null
      : await resolveStoreFile(callerOf(deps, req), kbId, recordId);
  if (file === null) {
    sendError(
      res,
      404,
      `No file found with id '${fileId}' in vector store '${toVectorStoreId(kbId)}'.`,
      'not_found',
      'file_id',
    );
    return null;
  }
  return { kbId, fileId, file };
}

export const retrieveVectorStoreFile =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    try {
      const found = await storeFileParam(deps, req, res);
      if (found === null) return;
      res
        .status(200)
        .json(
          vectorStoreFileObject(
            toVectorStoreId(found.kbId),
            found.file,
            found.fileId,
          ),
        );
    } catch (error) {
      sendRunError(res, error);
    }
  };

export const removeVectorStoreFile =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    try {
      const found = await storeFileParam(deps, req, res);
      if (found === null) return;
      await deleteFile(callerOf(deps, req), found.file.recordId);
      res.status(200).json({
        id: found.fileId,
        object: 'vector_store.file.deleted',
        deleted: true,
      });
    } catch (error) {
      sendRunError(res, error);
    }
  };

const encodeBatchId = (fileIds: string[]): string =>
  `vsfb_${Buffer.from(JSON.stringify({ f: fileIds, n: randomBytes(3).toString('hex') })).toString('base64url')}`;

function decodeBatchId(batchId: unknown): string[] | null {
  if (typeof batchId !== 'string' || !batchId.startsWith('vsfb_')) return null;
  try {
    const parsed: unknown = JSON.parse(
      Buffer.from(batchId.slice(5), 'base64url').toString('utf8'),
    );
    const ids = isJson(parsed) && Array.isArray(parsed.f) ? parsed.f : null;
    return ids !== null && ids.every((id) => recordIdFromFileId(id) !== null)
      ? (ids as string[])
      : null;
  } catch {
    return null;
  }
}

async function batchObject(
  caller: ApiCaller,
  kbId: string,
  batchId: string,
  fileIds: string[],
): Promise<{ batch: Json; files: Json[] }> {
  const inStore = await listCollectionFiles(caller, kbId);
  const resolved: FileInfo[] = [];
  const files: Json[] = [];
  for (const fileId of fileIds) {
    const file = await resolveStoreFile(
      caller,
      kbId,
      recordIdFromFileId(fileId) ?? '',
      inStore,
    );
    if (file !== null) {
      resolved.push(file);
      files.push(vectorStoreFileObject(toVectorStoreId(kbId), file, fileId));
    }
  }
  const counts = fileCounts(resolved);
  counts.failed += fileIds.length - resolved.length;
  counts.total = fileIds.length;
  return {
    batch: {
      id: batchId,
      object: 'vector_store.files_batch',
      created_at: Math.floor(Date.now() / 1000),
      vector_store_id: toVectorStoreId(kbId),
      status:
        counts.in_progress > 0
          ? 'in_progress'
          : counts.failed > 0 && counts.completed === 0
            ? 'failed'
            : 'completed',
      file_counts: counts,
    },
    files,
  };
}

export const createFileBatch =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    const kbId = kbIdParam(req, res);
    if (kbId === null) return;
    const body = requestBody(req);
    const fileIds = Array.isArray(body.file_ids)
      ? body.file_ids.map(asString)
      : [];
    const recordIds = fileIdsOf(fileIds);
    if (
      recordIds === null ||
      recordIds.length === 0 ||
      recordIds.length > MAX_BATCH_FILES
    ) {
      sendError(
        res,
        400,
        `file_ids must hold 1 to ${String(MAX_BATCH_FILES)} file ids returned by POST /files`,
        'invalid_request',
        'file_ids',
      );
      return;
    }
    try {
      const caller = callerOf(deps, req);
      const files = await listCollectionFiles(caller, kbId);
      for (const recordId of recordIds)
        await addFile(caller, kbId, recordId, files);
      const batchId = encodeBatchId(fileIds);
      res
        .status(200)
        .json((await batchObject(caller, kbId, batchId, fileIds)).batch);
    } catch (error) {
      sendRunError(res, error);
    }
  };

function batchParam(req: Request, res: Response): string[] | null {
  const batchId = (req.params as Record<string, string | undefined>).batchId;
  const fileIds = decodeBatchId(batchId);
  if (fileIds === null) {
    sendError(
      res,
      404,
      `No file batch found with id '${batchId ?? ''}'.`,
      'not_found',
      'batch_id',
    );
  }
  return fileIds;
}

export const retrieveFileBatch =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    const kbId = kbIdParam(req, res);
    if (kbId === null) return;
    const fileIds = batchParam(req, res);
    if (fileIds === null) return;
    try {
      res
        .status(200)
        .json(
          (
            await batchObject(
              callerOf(deps, req),
              kbId,
              asString(req.params.batchId),
              fileIds,
            )
          ).batch,
        );
    } catch (error) {
      sendRunError(res, error);
    }
  };

/** Files are added when the batch is created, so there is nothing left to cancel. */
export const cancelFileBatch = retrieveFileBatch;

export const listFileBatchFiles =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    const kbId = kbIdParam(req, res);
    if (kbId === null) return;
    const fileIds = batchParam(req, res);
    if (fileIds === null) return;
    try {
      const { files } = await batchObject(
        callerOf(deps, req),
        kbId,
        asString(req.params.batchId),
        fileIds,
      );
      const filter =
        typeof req.query.filter === 'string' ? req.query.filter : '';
      res.status(200).json(
        listPage(
          files.filter((o) => filter === '' || o.status === filter),
          listParams(req.query, 20, 100),
        ),
      );
    } catch (error) {
      sendRunError(res, error);
    }
  };

export const searchVectorStore =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    const kbId = kbIdParam(req, res);
    if (kbId === null) return;
    const body = requestBody(req);
    const query = Array.isArray(body.query)
      ? body.query
          .map(asString)
          .filter((q) => q !== '')
          .join('\n')
      : asString(body.query);
    if (query.trim() === '') {
      sendError(
        res,
        400,
        'query is required',
        'missing_required_parameter',
        'query',
      );
      return;
    }
    const raw = Number(body.max_num_results);
    const limit = Number.isInteger(raw) && raw >= 1 && raw <= 50 ? raw : 10;
    try {
      const result = await apiJson(
        callerOf(deps, req),
        '/retrieve',
        {
          method: 'POST',
          json: {
            query,
            limit,
            filters: { apps: [kbId] },
            ...(body.rewrite_query === true ? { rewriteQuery: true } : {}),
          },
        },
        'Searching the vector store',
      );
      const chunks =
        isJson(result) && Array.isArray(result.chunks)
          ? result.chunks.filter(isJson)
          : [];
      const data = chunks
        .map((chunk) => searchResult(chunk, toFileId))
        .filter((r): r is Json => r !== null);
      res.status(200).json({
        object: 'vector_store.search_results.page',
        search_query: Array.isArray(body.query) ? body.query : [query],
        data,
        has_more: false,
        next_page: null,
      });
    } catch (error) {
      sendRunError(res, error);
    }
  };
