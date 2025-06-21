import { FileProcessingType } from './fp.constant';
import { RequestHandler } from 'express';

export interface FileProcessorConfiguration {
  fieldName: string;
  maxFileSize: number;
  allowedMimeTypes: string[];
  maxFilesAllowed: number;
  isMultipleFilesAllowed: boolean;
  processingType: FileProcessingType;
  strictFileUpload: boolean;
}

export interface FileBufferInfo {
  buffer: Buffer;
  originalname: string;
  mimetype: string;
  size: number;
  lastModified?: number,
}

export interface IFileUploadService {
  upload(): RequestHandler;
  processFiles(): RequestHandler;
  getMiddleware(): Array<RequestHandler>;
}

export interface CustomMulterFile extends Express.Multer.File {
  lastModified?: number;
}