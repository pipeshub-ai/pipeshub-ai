import fs from 'fs/promises';
import path from 'path';
import os from 'os';
import { injectable } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { StorageServiceInterface } from '../services/storage.service';
import {
  FilePayload,
  StorageServiceResponse,
  Document,
} from '../types/storage.service.types';
import {
  StorageError,
  StorageConfigurationError,
  StorageUploadError,
  StorageDownloadError,
  StorageNotFoundError,
  StorageValidationError,
  MultipartUploadError,
  PresignedUrlError,
} from '../../../libs/errors/storage.errors';
import { LocalStorageConfig } from '../config/storage.config';

@injectable()
class LocalStorageAdapter implements StorageServiceInterface {
  private readonly mountPath: string;
  private readonly baseUrl: string;
  private readonly mountName: string;
  private readonly logger = Logger.getInstance({
    service: 'LocalStorageAdapter',
  });

  constructor(credentials: LocalStorageConfig) {
    try {
      const { mountName, baseUrl } = credentials;

      // Validate required credentials
      if (!mountName || !baseUrl) {
        throw new StorageConfigurationError(
          'Missing required local storage configuration',
          {
            missingFields: {
              mountName: !mountName,
              baseUrl: !baseUrl,
            },
          },
        );
      }

      this.mountName = mountName;
      this.baseUrl = baseUrl;

      // Create a consistent mount path in the user's home directory
      // This mirrors how sync services like Dropbox typically work
      this.mountPath = this.createMountPath();

      // Ensure mount directory exists
      this.ensureMountExists();
      if (process.env.NODE_ENV == 'development') {
        this.logger.info('Local storage adapter initialized', {
          mountPath: this.mountPath,
          baseUrl: this.baseUrl,
          mountName: this.mountName,
        });
      }
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new StorageConfigurationError(
        'Failed to initialize local storage adapter',
        {
          originalError:
            error instanceof Error ? error.message : 'Unknown error',
        },
      );
    }
  }

  /**
   * Creates a consistent mount path based on the OS
   */
  private createMountPath(): string {
    const homeDir = os.homedir();

    // Handle different OS conventions
    switch (process.platform) {
      case 'darwin': // macOS
        return path.join(homeDir, 'Library', this.mountName);
      case 'win32': // Windows
        return path.join(homeDir, 'AppData', this.mountName);
      default: // Linux and others
        return path.join(homeDir, '.local', this.mountName);
    }
  }

  /**
   * Ensures the mount directory exists with proper permissions
   */
  private async ensureMountExists(): Promise<void> {
    try {
      await fs.mkdir(this.mountPath, { recursive: true, mode: 0o700 });
      if (process.env.NODE_ENV == 'development') {
        this.logger.info(`Mount point "${this.mountPath}" initialized`);
      }
    } catch (error) {
      throw new StorageConfigurationError('Failed to initialize mount point', {
        mountPath: this.mountPath,
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * Uploads a document to local storage
   */
  async uploadDocumentToStorageService(
    documentInPayload: FilePayload,
  ): Promise<StorageServiceResponse<string>> {
    try {
      this.validateFilePayload(documentInPayload);
      const relativePath = this.sanitizePath(documentInPayload.documentPath);
      const fullPath = path.join(this.mountPath, relativePath);
      const dirPath = path.dirname(fullPath);
      if (process.env.NODE_ENV == 'development') {
        this.logger.info('Uploading document to local storage', {
          path: fullPath,
          relativePath: relativePath,
          dirPath: dirPath,
        });
      }

      // Ensure directory exists
      await fs.mkdir(dirPath, { recursive: true });

      // Write file with proper permissions
      await fs.writeFile(fullPath, documentInPayload.buffer, { mode: 0o600 });

      const fileUrl = this.getFileUrl(relativePath);

      this.logger.info('Local storage upload successful');

      return {
        statusCode: 200,
        data: fileUrl,
      };
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new StorageUploadError(
        'Failed to upload document to local storage',
        {
          originalError:
            error instanceof Error ? error.message : 'Unknown error',
        },
      );
    }
  }

  /**
   * Updates an existing document's content
   */
  async updateBuffer(
    bufferDataInPayLoad: Buffer,
    document: Document,
  ): Promise<StorageServiceResponse<string>> {
    try {
      const localPath = this.getLocalPathFromUrl(document.local?.url);
      if (!localPath) {
        throw new StorageNotFoundError('Local file path not found');
      }

      const fullPath = path.join(this.mountPath, localPath);

      // Write updated content
      await fs.writeFile(fullPath, bufferDataInPayLoad, { mode: 0o600 });

      const fileUrl = this.getFileUrl(localPath);
      if (process.env.NODE_ENV == 'development') {
        this.logger.info('Local storage update successful', {
          path: localPath,
          url: fileUrl,
        });
      }

      return {
        statusCode: 200,
        data: fileUrl,
      };
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new StorageUploadError(
        'Failed to update document in local storage',
        {
          originalError:
            error instanceof Error ? error.message : 'Unknown error',
        },
      );
    }
  }

  /**
   * Retrieves document content
   */
  async getBufferFromStorageService(
    document: Document,
    version?: number,
  ): Promise<StorageServiceResponse<Buffer>> {
    try {
      const fileUrl =
        version === undefined || version === 0
          ? document.local?.localPath
          : document.versionHistory?.[version]?.local?.localPath;
      if (!fileUrl) {
        throw new StorageNotFoundError(
          'File URL not found for requested version',
        );
      }

      const localPath = this.getLocalPathFromUrl(fileUrl);
      if (!localPath) {
        throw new StorageNotFoundError('Invalid file URL format');
      }

      const fullPath = path.join(
        this.mountPath,
        version === undefined ? 'current' : 'versions',
        localPath,
      );

      // Read file content
      const buffer = await fs.readFile(fullPath);
      if (process.env.NODE_ENV == 'development') {
        this.logger.info('Local storage fetch successful', {
          path: localPath,
        });
      }
      return {
        statusCode: 200,
        data: buffer,
      };
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new StorageDownloadError(
        'Failed to get document from local storage',
        {
          originalError:
            error instanceof Error ? error.message : 'Unknown error',
        },
      );
    }
  }

  /**
   * Generates a URL for accessing the file
   */
  async getSignedUrl(
    document: Document,
    version?: number,
    _fileName?: string,
    _expirationTimeInSeconds: number = 3600,
  ): Promise<StorageServiceResponse<string>> {
    try {
      const fileUrl =
        version === undefined
          ? document.local?.url
          : document.versionHistory?.[version]?.local?.url;

      if (!fileUrl) {
        throw new StorageNotFoundError(
          'File URL not found for requested version',
        );
      }

      // For local storage, we just return the direct file path
      // In production, you might want to implement proper file serving
      return {
        statusCode: 200,
        data: fileUrl,
      };
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new PresignedUrlError('Failed to generate URL for local storage', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  // These methods are not implemented for local storage
  async getMultipartUploadId(): Promise<
    StorageServiceResponse<{ uploadId: string }>
  > {
    throw new MultipartUploadError(
      'Multipart upload not implemented for local storage',
      {
        suggestion: 'Use direct upload instead',
      },
    );
  }

  async generatePresignedUrlForPart(): Promise<
    StorageServiceResponse<{ url: string; partNumber: number }>
  > {
    throw new MultipartUploadError(
      'Multipart upload not implemented for local storage',
      {
        suggestion: 'Use direct upload instead',
      },
    );
  }

  async completeMultipartUpload(): Promise<
    StorageServiceResponse<{ url: string }>
  > {
    throw new MultipartUploadError(
      'Multipart upload not implemented for local storage',
      {
        suggestion: 'Use direct upload instead',
      },
    );
  }

  async generatePresignedUrlForDirectUpload(
    documentPath: string,
  ): Promise<StorageServiceResponse<{ url: string }>> {
    try {
      const relativePath = this.sanitizePath(documentPath);
      const fileUrl = this.getFileUrl(relativePath);
      return {
        statusCode: 200,
        data: { url: fileUrl },
      };
    } catch (error) {
      throw new PresignedUrlError(
        'Failed to generate direct upload URL for local storage',
        {
          originalError:
            error instanceof Error ? error.message : 'Unknown error',
        },
      );
    }
  }

  /**
   * Utility methods
   */
  private validateFilePayload(payload: FilePayload): void {
    if (!payload.buffer || !payload.documentPath || !payload.mimeType) {
      throw new StorageValidationError('Invalid file payload', {
        validation: {
          hasBuffer: !!payload.buffer,
          hasPath: !!payload.documentPath,
          hasMimeType: !!payload.mimeType,
        },
      });
    }
  }

  private getFileUrl(filePath: string): string {
    // Build the full local filesystem path
    const fullPath = path.join(this.mountPath, filePath);

    // Handle Windows paths specially
    if (process.platform === 'win32') {
      // Windows paths need an extra / after file:// and forward slashes
      return `file:///${fullPath.replace(/\\/g, '/')}`;
    }

    // Unix-like systems (macOS, Linux)
    return `file://${fullPath}`;
  }

  private getLocalPathFromUrl(url?: string): string | null {
    if (!url) return null;
    try {
      const urlObj = new URL(url);
      if (urlObj.protocol !== 'file:') {
        throw new Error('Not a file URL');
      }

      // Get the path portion and decode it
      let localPath = decodeURIComponent(urlObj.pathname);

      // Handle Windows paths
      if (process.platform === 'win32') {
        // Remove the leading / that file:// URLs have on Windows
        localPath = localPath.replace(/^\//, '');
      }

      // Get the relative path by removing the mount path and 'current' or 'versions' directory
      const relativePath = path.relative(
        path.join(this.mountPath, 'current'),
        localPath,
      );

      return relativePath;
    } catch {
      return null;
    }
  }

  private sanitizePath(filePath: string): string {
    // Remove any parent directory references for security
    const normalizedPath = path
      .normalize(filePath)
      .replace(/^(\.\.[\/\\])+/, '');
    return normalizedPath;
  }
}

export default LocalStorageAdapter;
