import { FileProcessingType, FileRejectionReason } from './fp.constant';
import { RequestHandler } from 'express';

export interface FileProcessorConfiguration {
  fieldName: string;
  maxFileSize: number;
  allowedMimeTypes: string[];
  maxFilesAllowed: number;
  isMultipleFilesAllowed: boolean;
  processingType: FileProcessingType;
  strictFileUpload: boolean;
  /**
   * Allowed file extensions (lower-case, no leading dot). Used as a fallback
   * when the browser reports an empty/generic MIME type (common for .md, .csv,
   * .svg). When omitted, only `allowedMimeTypes` is consulted.
   */
  allowedExtensions?: string[];
  /**
   * Opt-in: reject oversize / unsupported files individually instead of failing
   * the whole multipart batch. Rejected files are collected on
   * `req.body.rejectedFiles` so the handler can report them per-file.
   * Defaults to false (legacy whole-batch rejection) to preserve behavior for
   * routes that have not opted in.
   */
  partialUpload?: boolean;
  /**
   * Partial-upload only: ceiling on TOTAL bytes buffered for one multipart
   * request, bounding heap use regardless of file count. Defaults to 1 GB
   * (clamped up to at least `maxFileSize`). A request exceeding it is aborted.
   */
  maxRequestBytes?: number;
  /**
   * Optional resolver mapping a file extension (lower-case, no dot) to its
   * canonical MIME type. When provided, a parsed file whose browser-reported
   * MIME is not in `allowedMimeTypes` has its MIME normalized to the resolved
   * value (browsers send empty/generic MIME for .md, .csv, etc.). Keeps this
   * generic middleware decoupled from any concrete MIME table.
   */
  resolveMimeType?: (extension: string) => string | null | undefined;
}

/** A file dropped during processing, with a machine-readable reason. */
export interface RejectedFileInfo {
  originalname: string;
  filePath: string;
  size: number;
  mimetype: string;
  reason: FileRejectionReason;
  error: string;
}

export interface FileBufferInfo {
  buffer: Buffer;
  originalname: string;
  mimetype: string;
  size: number;
  lastModified: number;
  filePath: string;
}

export interface IFileUploadService {
  upload(): RequestHandler;
  processFiles(): RequestHandler;
  getMiddleware(): Array<RequestHandler>;
}

export interface CustomMulterFile extends Express.Multer.File {
  lastModified?: number;
  filePath?: string;
}