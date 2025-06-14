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

// Process file metadata including lastModified
private processFileMetadata(req: Request) {
  if (!req.file && (!req.files || (Array.isArray(req.files) && req.files.length === 0))) {
    return;
  }

  const files = req.file ? [req.file] : Array.isArray(req.files) ? req.files : [];
  
  // Get lastModified values from request body
  const lastModifiedValues = req.body.lastModified ? 
    (Array.isArray(req.body.lastModified) ? req.body.lastModified : [req.body.lastModified]) 
    : [];

  console.log('File Processor Middleware - Request body:', {
    lastModifiedValues,
    bodyKeys: Object.keys(req.body)
  });

  // Attach lastModified to each file
  files.forEach((file, index) => {
    // Parse the lastModified timestamp from string to number
    let lastModified: number;
    
    const lastModifiedValue = lastModifiedValues[index];
    
    console.log('File Processor Middleware - Processing file:', {
      fileName: file.originalname,
      lastModifiedValue: lastModifiedValue,
      lastModifiedType: typeof lastModifiedValue
    });
    
    try {
      if (lastModifiedValue) {
        // First try parsing as a number directly
        const timestamp = Number(lastModifiedValue);
        if (!isNaN(timestamp) && timestamp > 0) {
          lastModified = timestamp;
        } else {
          // If not a valid number, try parsing as a date string
          const date = new Date(lastModifiedValue);
          if (!isNaN(date.getTime())) {
            lastModified = date.getTime();
          } else {
            // If parsing fails, use current time
            lastModified = Date.now();
            console.warn('Failed to parse lastModified, using current time:', {
              fileName: file.originalname,
              lastModifiedValue,
              fallbackTime: lastModified
            });
          }
        }
      } else {
        // If no lastModified provided, use current time
        lastModified = Date.now();
        console.warn('No lastModified provided, using current time:', {
          fileName: file.originalname,
          fallbackTime: lastModified
        });
      }
    } catch (error) {
      // If parsing fails, use current time
      lastModified = Date.now();
      console.error('Error parsing lastModified, using current time:', {
        fileName: file.originalname,
        lastModifiedValue,
        error,
        fallbackTime: lastModified
      });
    }
    
    console.log('File Processor Middleware - Final result:', {
      fileName: file.originalname,
      lastModifiedValue: lastModifiedValue,
      parsedLastModified: lastModified,
      isValidTimestamp: lastModified > 0
    });
    
    // Store the parsed timestamp on the file object
    (file as any).lastModified = lastModified;
  });
}

  public validateJSONContent() {
    return (req: Request, _res: Response, next: NextFunction) => {
      // Process file metadata first
      this.processFileMetadata(req);

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
