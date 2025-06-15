import { Request, Response, NextFunction } from 'express';
import multer from 'multer';
import { BadRequestError } from '../../errors/http.errors';

const defaultOptions: FileUploadOptions = {
  allowedMimeTypes: ['application/json'],
  maxFileSize: 1024 * 1024 * 5, // 5MB
  maxFiles: 1,
  fileFieldName: 'file',
  validateFileContent: false,
};

export interface FileUploadOptions {
  allowedMimeTypes: string[];
  maxFileSize?: number;
  maxFiles?: number;
  fileFieldName?: string;
  validateFileContent?: boolean;
}

export class FileUploadMiddleware {
  private options: FileUploadOptions;
  private multer: multer.Multer;

  constructor(options: FileUploadOptions) {
    this.options = {
      ...defaultOptions,
      ...options,
    };

    const storage = multer.memoryStorage();

    const fileExtensionFilter = (
      _req: Request,
      file: Express.Multer.File,
      cb: multer.FileFilterCallback,
    ) => {
      // check mimetype
      if (this.options.allowedMimeTypes.includes(file.mimetype)) {
        cb(null, true);
      } else {
        return cb(
          new BadRequestError(
            `Invalid file type. Allowed types: ${this.options.allowedMimeTypes.join(', ')}`,
          ),
        );
      }
    };

    this.multer = multer({
      storage: storage,
      limits: {
        fileSize: this.options.maxFileSize,
        files: this.options.maxFiles,
      },
      fileFilter: fileExtensionFilter,
    });
  }

  // Handle single file upload
  public singleFileUpload() {
    return this.multer.single(this.options.fileFieldName!);
  }

  // Handle multiple file upload
  public multipleFileUpload() {
    return this.multer.array(
      this.options.fileFieldName!,
      this.options.maxFiles,
    );
  }

  public validateJSONContent() {
    return (req: Request, _res: Response, next: NextFunction) => {
      if (
        !req.file &&
        (!req.files || (Array.isArray(req.files) && req.files.length === 0))
      ) {
        return next();
      }

      const files = req.file
        ? [req.file]
        : Array.isArray(req.files)
          ? req.files
          : [];

      if (files.length === 0) {
        return next();
      }

      try {
        // Process each file
        files.forEach((file, index) => {
          // Try parsing the file content as JSON
          const fileContent = file.buffer.toString('utf-8');
          const jsonData = JSON.parse(fileContent);

          // For single file uploads, attach directly
          if (files.length === 1) {
            req.body.fileConfig = jsonData;
          }
          // For multiple files, create an array
          else {
            if (!req.body.fileConfigs) {
              req.body.fileConfigs = [];
            }
            req.body.fileConfigs[index] = jsonData;
          }
        });

        next();
      } catch (error) {
        next(new BadRequestError('Invalid JSON format in uploaded file'));
      }
    };
  }
}
