import multer from 'multer';
import {
  CustomMulterFile,
  FileBufferInfo,
  FileProcessorConfiguration,
  IFileUploadService,
} from './fp.interface';
import { BadRequestError, NotImplementedError } from '../../errors/http.errors';
import { NextFunction, RequestHandler, Request, Response } from 'express';
import { FileProcessingType } from './fp.constant';
import { Logger } from '../../services/logger.service';
import { AuthenticatedUserRequest } from '../types';

const logger = Logger.getInstance({ service: 'FileProcessorService' });

export class FileProcessorService implements IFileUploadService {
  protected readonly multerUpload: multer.Multer;
  protected readonly configuration: FileProcessorConfiguration;

  constructor(configuration: FileProcessorConfiguration) {
    this.configuration = configuration;
    this.multerUpload = multer({
      storage: multer.memoryStorage(),
      limits: {
        fileSize: this.configuration.maxFileSize,
        files: this.configuration.maxFilesAllowed,
      },
      fileFilter: (_req, file, callback) => {
        if (this.configuration.allowedMimeTypes.includes(file.mimetype)) {
          callback(null, true);
        } else {
          return callback(
            new BadRequestError(
              `Invalid file type. Allowed types: ${this.configuration.allowedMimeTypes.join(', ')}`,
            ),
          );
        }
      },
    });
    logger.debug('FileProcessorService initialized', {
      configuration: this.configuration,
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
        this.processFileMetadata(req, files);

        // If strict mode and no files, throw an error
        if (files.length === 0) {
          if (this.configuration.strictFileUpload) {
            return next(
              new BadRequestError(
                'File upload required but no files were received',
              ),
            );
          } else {
            logger.debug('No files to process, skipping file processing');
            return next();
          }
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
        if (this.configuration.strictFileUpload) {
          return next(new BadRequestError('No files available for processing'));
        }
        // If not strict, just skip processing
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
      if (this.configuration.isMultipleFilesAllowed) {
        req.body.fileBuffers = files
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
              mimetype: file.mimetype,
              size: file.size,
              lastModified: lastModified,
              filePath: filePath,
            } as FileBufferInfo;
          })
          .filter(Boolean);
        logger.debug('Processed multiple buffer files', {
          count: files.length,
        });
      } else if (files.length === 1) {
        const file = files[0];
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
            mimetype: file.mimetype,
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
