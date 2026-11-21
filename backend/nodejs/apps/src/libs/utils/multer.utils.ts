import { randomUUID } from 'node:crypto';
import { mkdirSync, rmSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { NextFunction, Request, RequestHandler, Response } from 'express';
import multer from 'multer';

import { LargePayloadError } from '../errors/http.errors';

/**
 * Every multipart reader in the API is built here (SEC-C-08), so each one is metered the same way: the bytes of
 * a request are counted as they arrive, chunked or not, and the request is refused past its cap. The declared
 * `Content-Length` is only a fast path; a chunked request sends none.
 */
export interface MeteredMulterOptions extends multer.Options {
  /**
   * The most bytes one request may carry, files and fields together. When unset it is derived from
   * `limits.files` x `limits.fileSize` plus the fields' allowance; with neither set, no total is enforced.
   */
  maxRequestBytes?: number;
  /** The refusal past the cap; the default says how large a request may be. */
  tooLarge?: (maxBytes: number) => Error;
}

/** What the non-file fields of a request may add to a derived cap. */
export const FIELDS_ALLOWANCE_BYTES = 16 * 1024 * 1024;

const defaultTooLarge = (maxBytes: number): Error =>
  new LargePayloadError(
    `The upload is larger than ${Math.ceil(maxBytes / (1024 * 1024)).toLocaleString('en-US')} MiB. Send fewer or smaller files.`,
  );

export function requestByteCap(
  options: MeteredMulterOptions,
): number | undefined {
  if (options.maxRequestBytes !== undefined) return options.maxRequestBytes;
  const files = options.limits?.files;
  const fileSize = options.limits?.fileSize;
  if (files === undefined || fileSize === undefined) return undefined;
  if (!Number.isFinite(files) || !Number.isFinite(fileSize)) return undefined;
  return files * fileSize + FIELDS_ALLOWANCE_BYTES;
}

/**
 * `upload` with the request's bytes counted as they arrive. Past `maxBytes` the request is refused at once, the
 * rest is never read, and the connection closes after the refusal, so a body of undeclared length cannot be
 * buffered or written past the cap.
 */
export function meterRequestBytes(
  upload: RequestHandler,
  maxBytes: number,
  tooLarge: (maxBytes: number) => Error = defaultTooLarge,
): RequestHandler {
  return (req: Request, res: Response, next: NextFunction): void => {
    const declared = Number(req.headers['content-length']);
    if (Number.isFinite(declared) && declared > maxBytes) {
      res.setHeader('Connection', 'close');
      next(tooLarge(maxBytes));
      return;
    }
    let seen = 0;
    let settled = false;
    const settle = (error?: unknown): void => {
      if (settled) return;
      settled = true;
      req.off('data', count);
      next(error);
    };
    const count = (chunk: Buffer): void => {
      seen += chunk.length;
      if (seen <= maxBytes) return;
      req.unpipe();
      req.pause();
      res.setHeader('Connection', 'close');
      // The reader's own abort would drain the rest of the body first; an endless one never ends.
      res.once('finish', () => req.destroy());
      settle(tooLarge(maxBytes));
    };
    // Attached in the same tick as the reader pipes the request, so no chunk is seen by one and not the other.
    req.on('data', count);
    void upload(req, res, settle);
  };
}

const meteredMulter = (
  inner: multer.Multer,
  maxBytes: number | undefined,
  tooLarge?: (n: number) => Error,
): multer.Multer => {
  if (maxBytes === undefined) return inner;
  const wrap =
    <A extends unknown[]>(make: (...args: A) => RequestHandler) =>
    (...args: A): RequestHandler =>
      meterRequestBytes(make(...args), maxBytes, tooLarge);
  return {
    single: wrap(inner.single.bind(inner)),
    array: wrap(inner.array.bind(inner)),
    fields: wrap(inner.fields.bind(inner)),
    any: wrap(inner.any.bind(inner)),
    none: wrap(inner.none.bind(inner)),
  };
};

// multer decodes the multipart `filename` as Latin-1 unless told otherwise,
// turning "résumé.pdf" into "rÃ©sumÃ©.pdf". Browsers send it as raw UTF-8.
// @types/multer does not declare `defParamCharset`, hence the widened type.
export function createMulter(
  options: MeteredMulterOptions = {},
): multer.Multer {
  const { maxRequestBytes: _cap, tooLarge, ...rest } = options;
  const withUtf8Names: multer.Options & { defParamCharset: string } = {
    ...rest,
    defParamCharset: 'utf8',
  };
  return meteredMulter(
    multer(withUtf8Names),
    requestByteCap(options),
    tooLarge,
  );
}

/** The folder a request's uploaded files are streamed into, removed with everything in it once the response ends. */
const requestUploadDir = (req: Request, res: Response): string => {
  const holder = req as Request & { uploadDir?: string };
  if (holder.uploadDir === undefined) {
    const dir = join(tmpdir(), `pipeshub-upload-${randomUUID()}`);
    mkdirSync(dir, { recursive: true, mode: 0o700 });
    holder.uploadDir = dir;
    res.once('close', () => rmSync(dir, { recursive: true, force: true }));
  }
  return holder.uploadDir;
};

/**
 * Files streamed to a private temporary folder instead of held in memory (SEC-C-08): the process holds one
 * chunk of an upload at a time, however many files it carries. Read each with `readUploadedFile`.
 */
export function requestDiskStorage(): multer.StorageEngine {
  return multer.diskStorage({
    destination: (req, _file, cb) => {
      try {
        cb(null, requestUploadDir(req, req.res as Response));
      } catch (error) {
        cb(error as Error, '');
      }
    },
    filename: (_req, _file, cb) => cb(null, randomUUID()),
  });
}

/** One uploaded file's bytes: from disk when streamed there, else the buffer memory storage kept. */
export function readUploadedFile(file: Express.Multer.File): Promise<Buffer> {
  return file.path ? readFile(file.path) : Promise.resolve(file.buffer);
}
