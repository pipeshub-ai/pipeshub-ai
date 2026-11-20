import { Request, Response } from 'express';
import { Readable } from 'stream';

import { ChatRunError } from '../utils/chat-run';
import {
  deleteFile,
  downloadFile,
  filesCollection,
  getFile,
  listCollectionFiles,
  uploadFile,
} from '../utils/collections';
import { asString, recordIdFromFileId } from '../utils/openai-format';
import { fileObject, listPage, listParams } from '../utils/openai-objects';
import { ApiCaller } from '../utils/pipeshub-api';
import {
  OpenAICompatDeps,
  requestBody,
  sendError,
  sendRunError,
} from './openai_compat.controller';

type Handler = (req: Request, res: Response) => Promise<void>;

export function callerOf(deps: OpenAICompatDeps, req: Request): ApiCaller {
  return {
    baseUrl: deps.appConfig.esBackend,
    authorization: req.headers.authorization ?? '',
  };
}

/** The record behind `:fileId`, or null after answering 404 for an id we did not issue. */
export function recordIdParam(
  req: Request,
  res: Response,
  name = 'fileId',
): string | null {
  const params = req.params as Record<string, string | undefined>;
  const recordId = recordIdFromFileId(params[name]);
  if (recordId === null) {
    sendError(
      res,
      404,
      `No such File object: ${params[name] ?? ''}`,
      'not_found',
      name === 'fileId' ? 'file_id' : name,
    );
  }
  return recordId;
}

export const createFile =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    const file = (req as Request & { file?: Express.Multer.File }).file;
    if (file === undefined) {
      sendError(
        res,
        400,
        "'file' is a required property",
        'missing_required_parameter',
        'file',
      );
      return;
    }
    const purpose = asString(requestBody(req).purpose) || 'user_data';
    try {
      const caller = callerOf(deps, req);
      const kb = await filesCollection(caller, true);
      if (kb === null)
        throw new ChatRunError('No files collection', 502, 'server_error');
      const recordId = await uploadFile(
        caller,
        kb.id,
        file.originalname,
        new Uint8Array(file.buffer),
        file.mimetype,
      );
      const info = await getFile(caller, recordId);
      res
        .status(200)
        .json(fileObject({ ...info, filename: file.originalname }, purpose));
    } catch (error) {
      sendRunError(res, error);
    }
  };

export const listFiles =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    try {
      const caller = callerOf(deps, req);
      const kb = await filesCollection(caller, false);
      const files = kb === null ? [] : await listCollectionFiles(caller, kb.id);
      res.status(200).json(
        listPage(
          files.map((f) => fileObject(f)),
          listParams(req.query, 10000, 10000),
        ),
      );
    } catch (error) {
      sendRunError(res, error);
    }
  };

export const retrieveFile =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    const recordId = recordIdParam(req, res);
    if (recordId === null) return;
    try {
      res
        .status(200)
        .json(fileObject(await getFile(callerOf(deps, req), recordId)));
    } catch (error) {
      sendRunError(res, error);
    }
  };

export const fileContent =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    const recordId = recordIdParam(req, res);
    if (recordId === null) return;
    try {
      const upstream = await downloadFile(callerOf(deps, req), recordId);
      res.status(200);
      for (const header of [
        'content-type',
        'content-length',
        'content-disposition',
      ]) {
        const value = upstream.headers.get(header);
        if (value !== null) res.setHeader(header, value);
      }
      if (upstream.body === null) {
        res.end();
        return;
      }
      Readable.fromWeb(
        upstream.body as Parameters<typeof Readable.fromWeb>[0],
      ).pipe(res);
    } catch (error) {
      sendRunError(res, error);
    }
  };

export const removeFile =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    const recordId = recordIdParam(req, res);
    if (recordId === null) return;
    try {
      await deleteFile(callerOf(deps, req), recordId);
      res
        .status(200)
        .json({ id: `file-${recordId}`, object: 'file', deleted: true });
    } catch (error) {
      sendRunError(res, error);
    }
  };
