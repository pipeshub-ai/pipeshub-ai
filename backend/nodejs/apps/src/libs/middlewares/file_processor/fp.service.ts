import multer from 'multer';
import {
  CustomMulterFile,
  FileBufferInfo,
  FileProcessorConfiguration,
  IFileUploadService,
  RejectedFileInfo,
} from './fp.interface';
import { BadRequestError, NotImplementedError } from '../../errors/http.errors';
import { NextFunction, RequestHandler, Request, Response } from 'express';
import { FileProcessingType, FileRejectionReason } from './fp.constant';
import { Logger } from '../../services/logger.service';
import { AuthenticatedUserRequest } from '../types';
import { getFileExtension } from '../../utils/file-extension.util';

const logger = Logger.getInstance({ service: 'FileProcessorService' });

// Internal request property used to accumulate per-file rejections recorded
// during the post-parse processing step (in partial-upload mode the multer
// fileFilter accepts every file; all rejection decisions happen post-parse so
// each carries the resolved `filePath`). Kept off req.body so multer's field
// parsing cannot clobber it.
const REJECTED_FILES_KEY = '__fpRejectedFiles';

/**
 * Memory storage that enforces a hard per-file byte ceiling WITHOUT aborting the
 * whole multipart batch.
 *
 * `multer`'s built-in `limits.fileSize` cannot be used in partial-upload mode:
 * it fails the ENTIRE request (`LIMIT_FILE_SIZE`) on the first oversize file,
 * which is exactly the whole-batch abort partial uploads exist to avoid. But
 * simply dropping `limits.fileSize` (the previous approach) left `memoryStorage`
 * buffering every byte of every file into the Node heap before the post-parse
 * size check ran — an unbounded, authenticated memory-exhaustion vector
 * (maxFilesAllowed files x arbitrary size each).
 *
 * This engine keeps the partial-batch semantics while restoring the per-file
 * memory bound: once a file crosses `maxFileSize`, its buffered chunks are freed
 * and further bytes are drained-and-discarded (we still consume the stream so
 * busboy advances to the next file). The file's reported `size` is the true
 * byte count, so the existing post-parse `size > maxFileSize` check still flags
 * it as EXCEEDS_SIZE_LIMIT — the discarded buffer is never used because rejected
 * files are filtered out before buffers are read.
 *
 * Peak buffered memory is therefore bounded to ~maxFileSize per in-flight file
 * instead of the full (attacker-controlled) payload size.
 */
function createCappedMemoryStorage(
  maxFileSize: number,
  maxRequestBytes: number,
): multer.StorageEngine {
  // The per-request running total is tracked on the REQUEST object, not in this
  // closure: the storage engine may be created once and reused (the
  // FileProcessorService can be long-lived / singleton), so a closure counter
  // would accumulate across requests and eventually block every upload. Keying
  // off `_req` keeps the engine stateless and request-scoped. busboy parses the
  // parts of a single request sequentially, so this counter needs no locking.
  //
  // Bounds total transferred bytes so a single multipart request cannot hold
  // maxFilesAllowed x maxFileSize in the heap (e.g. 1000 x 30 MB = 30 GB). A
  // request that exceeds the ceiling is aborted — legitimate client batches
  // (tens of files) are far below it; only an abusive/misconfigured client hits
  // it, and it can simply retry with smaller batches.
  return {
    _handleFile(_req, file, cb) {
      const reqState = _req as unknown as { __cappedRequestBytes?: number };
      let chunks: Buffer[] = [];
      let size = 0;
      let exceeded = false;
      let finished = false;

      const stream = (file as unknown as { stream: NodeJS.ReadableStream })
        .stream;

      const fail = (err: Error): void => {
        if (finished) return;
        finished = true;
        chunks = [];
        cb(err);
      };

      stream.on('data', (chunk: Buffer) => {
        if (finished) return;
        size += chunk.length;
        const requestBytes = (reqState.__cappedRequestBytes ?? 0) + chunk.length;
        reqState.__cappedRequestBytes = requestBytes;

        if (requestBytes > maxRequestBytes) {
          // Whole-request budget blown → abort (multer fails the request).
          fail(
            new BadRequestError(
              `Upload request exceeds the maximum total size of ${Math.round(
                maxRequestBytes / (1024 * 1024),
              )} MB`,
            ),
          );
          return;
        }
        if (size > maxFileSize) {
          // Per-file cap: stop accumulating and free what we have; keep draining
          // (and counting) so busboy advances and `size` stays accurate.
          if (!exceeded) {
            exceeded = true;
            chunks = [];
          }
          return;
        }
        chunks.push(chunk);
      });
      stream.on('error', (err: Error) => fail(err));
      stream.on('end', () => {
        if (finished) return;
        finished = true;
        cb(null, {
          buffer: exceeded ? Buffer.alloc(0) : Buffer.concat(chunks),
          size,
        });
      });
    },
    _removeFile(_req, _file, cb) {
      cb(null);
    },
  };
}

// Default ceiling on total bytes buffered for a single multipart request in
// partial-upload mode. Generous enough that real client batches (tens of files)
// never hit it, while capping the worst case far below an OOM.
const DEFAULT_MAX_REQUEST_BYTES = 1024 * 1024 * 1024; // 1 GB

export class FileProcessorService implements IFileUploadService {
  protected readonly multerUpload: multer.Multer;
  protected readonly configuration: FileProcessorConfiguration;

  constructor(configuration: FileProcessorConfiguration) {
    this.configuration = configuration;

    // In partial-upload mode, per-file SIZE is enforced AFTER multer parses each
    // file, so a single oversize file does not abort the whole batch. To keep
    // that behavior WITHOUT letting an oversize file balloon the Node heap
    // (memoryStorage buffers the whole file before the post-parse check runs),
    // use a capped memory storage that drops bytes past the limit. Legacy
    // whole-batch mode keeps multer's hard per-file `limits.fileSize`, which
    // rejects the entire request on the first oversize file.
    const limits: multer.Options['limits'] = {
      files: this.configuration.maxFilesAllowed,
    };
    if (!this.configuration.partialUpload) {
      limits.fileSize = this.configuration.maxFileSize;
    }

    const maxRequestBytes = Math.max(
      this.configuration.maxRequestBytes ?? DEFAULT_MAX_REQUEST_BYTES,
      this.configuration.maxFileSize,
    );
    const storage = this.configuration.partialUpload
      ? createCappedMemoryStorage(this.configuration.maxFileSize, maxRequestBytes)
      : multer.memoryStorage();

    this.multerUpload = multer({
      storage,
      limits,
      fileFilter: (_req, file, callback) => {
        if (this.configuration.partialUpload) {
          // Defer ALL acceptance decisions (type AND size) to the post-parse
          // step, which runs after file metadata is attached so each rejection
          // carries the resolved `filePath` (needed to report per-file failures
          // — two `.DS_Store` files in different folders must stay distinct).
          // Accept here so one bad file never aborts the batch.
          return callback(null, true);
        }
        // Legacy whole-batch mode: reject the request on the first bad file.
        if (this.isAllowedType(file)) {
          return callback(null, true);
        }
        return callback(
          new BadRequestError(
            `Invalid file type. Allowed types: ${this.configuration.allowedMimeTypes.join(', ')}`,
          ),
        );
      },
    });
    logger.debug('FileProcessorService initialized', {
      configuration: this.configuration,
    });
  }

  /**
   * Whether a file's type is supported.
   *
   * When an extension allowlist is configured (e.g. KB uploads pass every
   * supported extension), it is AUTHORITATIVE: the persisted record is keyed off
   * the file extension and downstream validation rejects any file whose
   * extension has no known MIME type. The browser-reported MIME is deliberately
   * NOT trusted here — a generic value like `application/octet-stream` happens to
   * be in the allowed set and would otherwise smuggle in unsupported-extension
   * files such as `.DS_Store`. Browsers report empty/generic MIME for some
   * supported types (.md, .csv, .svg), so the extension is also the more
   * reliable signal for the accept case.
   *
   * When no extension allowlist is configured, fall back to the MIME allowlist.
   */
  private isAllowedType(file: Express.Multer.File): boolean {
    const allowedExtensions = this.configuration.allowedExtensions;
    if (allowedExtensions && allowedExtensions.length > 0) {
      const ext = getFileExtension(file.originalname);
      return !!ext && allowedExtensions.includes(ext);
    }
    return this.configuration.allowedMimeTypes.includes(file.mimetype);
  }

  private getRejectionList(req: Request): RejectedFileInfo[] {
    const holder = req as unknown as Record<string, RejectedFileInfo[]>;
    if (!Array.isArray(holder[REJECTED_FILES_KEY])) {
      holder[REJECTED_FILES_KEY] = [];
    }
    return holder[REJECTED_FILES_KEY];
  }

  private rejectionMessage(
    reason: FileRejectionReason,
    extension: string | null,
  ): string {
    switch (reason) {
      case FileRejectionReason.EXCEEDS_SIZE_LIMIT: {
        const mb = Math.round(this.configuration.maxFileSize / (1024 * 1024));
        return `File exceeds the ${mb} MB size limit`;
      }
      case FileRejectionReason.UNSUPPORTED_TYPE:
        return extension
          ? `Unsupported file type ".${extension}"`
          : 'Unsupported file type';
      default:
        return 'File rejected';
    }
  }

  /**
   * The MIME type to persist for a parsed file: the browser value when it is
   * already allowed, otherwise the extension-resolved value (so files browsers
   * report with empty/generic MIME — .md, .csv — still carry a valid MIME).
   */
  private effectiveMimeType(file: Express.Multer.File): string {
    if (this.configuration.allowedMimeTypes.includes(file.mimetype)) {
      return file.mimetype;
    }
    const ext = getFileExtension(file.originalname);
    const resolved = ext ? this.configuration.resolveMimeType?.(ext) : null;
    return resolved || file.mimetype;
  }

  private recordRejection(
    req: Request,
    file: Express.Multer.File,
    reason: FileRejectionReason,
  ): void {
    const customFile = file as CustomMulterFile;
    const ext = getFileExtension(file.originalname);
    this.getRejectionList(req).push({
      originalname: file.originalname,
      filePath: customFile.filePath ?? file.originalname,
      size: file.size ?? 0,
      mimetype: file.mimetype,
      reason,
      error: this.rejectionMessage(reason, ext),
    });
  }

  upload(): RequestHandler {
    return (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      // Check if content-type contains multipart/form-data
      const isMultipart =
        req.headers['content-type']?.includes('multipart/form-data') || false;
      // Only try to process files if this is a multipart request
      if (!isMultipart) {
        logger.debug(
          'Not a multipart/form-data request, skipping file processing',
        );
        return next();
      }

      logger.debug('Processing upload with multer');
      const fieldName = Array.isArray(this.configuration.fieldName)
        ? this.configuration.fieldName[0]
        : this.configuration.fieldName;

      logger.debug('Using field name for upload', { fieldName });

      // Use the appropriate multer upload method based on configuration
      const uploadHandler = this.configuration.isMultipleFilesAllowed
        ? this.multerUpload.array(fieldName, this.configuration.maxFilesAllowed)
        : this.multerUpload.single(fieldName);

      // Use the selected upload handler with proper error handling and continuation
      uploadHandler(req, res, (err: any) => {
        if (err) {
          logger.error('upload middleware failed with error: ', err.message);
          return next(
            new BadRequestError(
              `File upload failed: ${err.message || 'Unknown error'}`,
            ),
          );
        }

        // Now check if files were actually uploaded
        const files = this.getFiles(req);
        logger.debug('Files after Multer processing', {
          count: files.length,
          fileNames: files.map((f) => f.originalname),
        });

        // Process file metadata (including lastModified) immediately after multer processing
        try {
          this.processFileMetadata(req, files);
        } catch (metadataError) {
          logger.error('File metadata processing failed', { error: metadataError });
          return next(metadataError);
        }

        // If strict mode and no files, throw an error — UNLESS this is a
        // partial upload where every file was soft-rejected (e.g. all
        // unsupported types). In that case continue so the handler can report
        // the per-file failures instead of failing the whole request.
        if (files.length === 0) {
          const hasRejections =
            this.configuration.partialUpload &&
            this.getRejectionList(req).length > 0;
          if (this.configuration.strictFileUpload && !hasRejections) {
            return next(
              new BadRequestError(
                'File upload required but no files were received',
              ),
            );
          }
          if (!hasRejections) {
            logger.debug('No files to process, skipping file processing');
          }
          return next();
        }

        // multer layer completed, moving to next layer
        logger.debug('Multer upload completed successfully');
        next();
      });
    };
  }

  /**
   * Process file metadata from files_metadata JSON.
   * Expected format: JSON array with { file_path, last_modified } objects
   * Each entry corresponds to a file by index.
   */
  private processFileMetadata(
    req: Request,
    files: Express.Multer.File[],
  ): void {
    if (files.length === 0) {
      return;
    }

    logger.debug('Processing file metadata', {
      filesCount: files.length,
      hasFilesMetadata: !!req.body.files_metadata,
    });

    // Parse files_metadata JSON
    let filesMetadata: Array<{ file_path: string; last_modified: number }> = [];

    if (req.body.files_metadata) {
      try {
        filesMetadata = JSON.parse(req.body.files_metadata);
      } catch (error) {
        logger.error('Failed to parse files_metadata JSON', { error });
        throw new BadRequestError(
          'Invalid files_metadata format. Expected JSON array.',
        );
      }

      // Validate metadata count matches files count
      if (filesMetadata.length !== files.length) {
        logger.error('Metadata count mismatch', {
          filesCount: files.length,
          metadataCount: filesMetadata.length,
        });
        throw new BadRequestError(
          `Metadata count mismatch: expected ${files.length} entries but got ${filesMetadata.length}`,
        );
      }
    }

    // Attach metadata to each file
    files.forEach((file, index) => {
      const metadata = filesMetadata[index];
      const customFile = file as CustomMulterFile;

      // Set file path (from metadata or fallback to original filename)
      customFile.filePath = metadata?.file_path || file.originalname;

      // Set lastModified (from metadata or fallback to current time)
      if (metadata?.last_modified) {
        const timestamp = Number(metadata.last_modified);
        customFile.lastModified =
          !isNaN(timestamp) && timestamp > 0 ? timestamp : Date.now();
      } else {
        customFile.lastModified = Date.now();
      }

      logger.debug('File metadata processed', {
        fileName: file.originalname,
        filePath: customFile.filePath,
        lastModified: customFile.lastModified,
      });
    });
  }

  processFiles(): RequestHandler {
    return (
      req: AuthenticatedUserRequest,
      _res: Response,
      next: NextFunction,
    ) => {
      logger.debug('Starting file processing');
      const files = this.getFiles(req);

      if (files.length === 0) {
        logger.debug('No files to process', {
          strictFileUpload: this.configuration.strictFileUpload,
        });
        const rejected = this.getRejectionList(req);
        const hasRejections =
          this.configuration.partialUpload && rejected.length > 0;
        if (this.configuration.strictFileUpload && !hasRejections) {
          return next(new BadRequestError('No files available for processing'));
        }
        // Partial upload with only rejected files: hand off to the controller
        // with an empty accepted set plus the rejection details.
        if (this.configuration.partialUpload) {
          req.body.rejectedFiles = rejected;
          if (this.configuration.isMultipleFilesAllowed) {
            req.body.fileBuffers = [];
          }
        }
        return next();
      }

      try {
        switch (this.configuration.processingType) {
          case FileProcessingType.JSON:
            logger.debug('Processing JSON File', { count: files.length });
            this.processJsonFiles(req, files);
            break;
          case FileProcessingType.BUFFER:
            logger.debug('Processing BUFFER File', { count: files.length });
            this.processBufferFiles(req, files);
            break;
          default:
            throw new NotImplementedError('Processing type not implemented');
        }
        logger.debug('File processing completed successfully');
        return next();
      } catch (error) {
        let errorMessage = 'Error processing file';

        if (this.configuration.processingType === FileProcessingType.JSON) {
          errorMessage = 'Invalid JSON format in uploaded file';
        }
        return next(new BadRequestError(errorMessage));
      }
    };
  }

  getMiddleware(): Array<RequestHandler> {
    return [this.upload(), this.processFiles()];
  }

  private processJsonFiles(
    req: AuthenticatedUserRequest,
    files: Express.Multer.File[],
  ): void {
    try {
      if (this.configuration.isMultipleFilesAllowed) {
        req.body.fileContents = files.map((file) =>
          JSON.parse(file.buffer.toString('utf-8')),
        );
      } else {
        req.body.fileContent = JSON.parse(
          files[0]?.buffer?.toString('utf-8') as string,
        );
      }
    } catch (error) {
      logger.error('Error parsing JSON file', { error });
      throw new BadRequestError('Invalid JSON file');
    }
  }

  private processBufferFiles(
    req: AuthenticatedUserRequest,
    files: Express.Multer.File[],
  ): void {
    logger.debug('processBufferFiles', {
      isMultipleFilesAllowed: this.configuration.isMultipleFilesAllowed,
      files: files.map((file) => file.originalname),
    });

    try {
      // In partial-upload mode, enforce per-file rules HERE (rather than in the
      // multer fileFilter) so each rejection carries the resolved `filePath`,
      // and a single bad file never aborts the batch. Unsupported types and
      // oversize files are recorded as per-file failures with a stable reason
      // code; everything else proceeds.
      let acceptedFiles = files;
      if (this.configuration.partialUpload) {
        acceptedFiles = files.filter((file) => {
          if (!this.isAllowedType(file)) {
            this.recordRejection(
              req,
              file,
              FileRejectionReason.UNSUPPORTED_TYPE,
            );
            return false;
          }
          if ((file.size ?? 0) > this.configuration.maxFileSize) {
            this.recordRejection(
              req,
              file,
              FileRejectionReason.EXCEEDS_SIZE_LIMIT,
            );
            return false;
          }
          return true;
        });
        // Always surface the rejection list so the handler can report failures.
        req.body.rejectedFiles = this.getRejectionList(req);
      }

      if (this.configuration.isMultipleFilesAllowed) {
        req.body.fileBuffers = acceptedFiles
          .map((file) => {
            if (!file) return null;
            const customFile = file as CustomMulterFile;
            const lastModified = customFile.lastModified ?? Date.now();
            const filePath = customFile.filePath ?? file.originalname;

            logger.debug('File Processor Service - Creating buffer info:', {
              fileName: file.originalname,
              filePath,
              lastModified,
            });

            return {
              originalname: file.originalname,
              buffer: file.buffer,
              mimetype: this.effectiveMimeType(file),
              size: file.size,
              lastModified: lastModified,
              filePath: filePath,
            } as FileBufferInfo;
          })
          .filter(Boolean);
        logger.debug('Processed multiple buffer files', {
          count: acceptedFiles.length,
        });
      } else if (acceptedFiles.length === 1) {
        const file = acceptedFiles[0];
        if (file) {
          const customFile = file as CustomMulterFile;
          const lastModified = customFile.lastModified ?? Date.now();
          const filePath = customFile.filePath ?? file.originalname;

          logger.debug(
            'File Processor Service - Creating single buffer info:',
            {
              fileName: file.originalname,
              filePath,
              lastModified,
            },
          );

          req.body.fileBuffer = {
            originalname: file.originalname,
            buffer: file.buffer,
            mimetype: this.effectiveMimeType(file),
            size: file.size,
            lastModified: lastModified,
            filePath: filePath,
          } as FileBufferInfo;
          logger.debug('Processed single buffer file');
        }
      } else {
        logger.warn('No files available to process in processBufferFiles');
      }
    } catch (error) {
      throw new BadRequestError('Invalid buffer file');
    }
  }

  private getFiles(req: Request): Express.Multer.File[] {
    try {
      // Handle case where we have a single file
      if (req.file) {
        return [req.file];
      } else if (req.files) {
        if (Array.isArray(req.files)) {
          return req.files;
        }

        // Handle non-array req.files (object with field names as keys)
        const fieldName = Array.isArray(this.configuration.fieldName)
          ? this.configuration.fieldName[0]
          : this.configuration.fieldName;

        const fieldFiles = req.files[fieldName];
        if (Array.isArray(fieldFiles)) {
          logger.debug('Found multiple files under field name', {
            count: fieldFiles,
          });
          return fieldFiles;
        } else if (fieldFiles) {
          // Handle case where fieldFiles might be a single file object
          logger.debug('Found single file under field name', {
            filename: fieldFiles,
          });
          return [fieldFiles];
        }
      }

      logger.debug('No files found in request');
      return [];
    } catch (error) {
      logger.error('Error getting files from request', { error });
      return [];
    }
  }
}
