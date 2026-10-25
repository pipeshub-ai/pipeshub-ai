import { constants as fsConstants } from 'fs';
import fs, { FileHandle } from 'fs/promises';
import path from 'path';
import os from 'os';
import { createHash, randomUUID } from 'crypto';
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

// Local filesystems cap one name at 255 bytes; object stores do not, so their
// keys keep the full name. Names that fit are kept byte for byte.
const MAX_FILE_NAME_BYTES = 255;
const MAX_KEPT_EXTENSION_BYTES = 32;

function truncateUtf8(text: string, maxBytes: number): string {
  let out = '';
  let used = 0;
  for (const char of text) {
    const size = Buffer.byteLength(char);
    if (used + size > maxBytes) break;
    out += char;
    used += size;
  }
  return out;
}

/**
 * One path segment as a local filesystem can hold it: unchanged when it fits in
 * 255 bytes, else a cut of it plus `~<sha256[:16]>` of the full segment, keeping
 * a short extension so the stored file still has its type.
 */
export function fitPathSegment(full: string): string {
  if (Buffer.byteLength(full) <= MAX_FILE_NAME_BYTES) {
    return full;
  }
  const ext = path.extname(full);
  const documentName = full.slice(0, full.length - ext.length);
  const suffix = `~${createHash('sha256').update(full).digest('hex').slice(0, 16)}`;
  if (Buffer.byteLength(ext) <= MAX_KEPT_EXTENSION_BYTES) {
    const room = MAX_FILE_NAME_BYTES - Buffer.byteLength(suffix + ext);
    return `${truncateUtf8(documentName, room)}${suffix}${ext}`;
  }
  return `${truncateUtf8(full, MAX_FILE_NAME_BYTES - suffix.length)}${suffix}`;
}

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
   * Writes beside the target and renames over it, so a write that fails part
   * way (disk full, storage gone) leaves the previous file whole instead of cut short.
   */
  private async writeFileAtomically(
    fullPath: string,
    data: Buffer,
  ): Promise<void> {
    // Named independently of the target: a long but valid filename plus a suffix
    // can pass the filesystem's limit for one name component.
    const tempPath = path.join(path.dirname(fullPath), `.${randomUUID()}.tmp`);
    try {
      await fs.writeFile(tempPath, data, { mode: 0o600 });
      await fs.rename(tempPath, fullPath);
    } catch (error) {
      await fs.rm(tempPath, { force: true }).catch(() => undefined);
      throw error;
    }
  }

  /**
   * Whether a file is stored at this path. Only a missing file answers false;
   * anything else (an unreadable folder, storage gone) rejects.
   */
  async objectExistsAtPath(documentPath: string): Promise<boolean> {
    const fullPath = this.assertInsideMount(
      path.join(this.mountPath, this.sanitizePath(documentPath)),
    );
    try {
      await fs.stat(fullPath);
      return true;
    } catch (error) {
      if ((error as { code?: string }).code === 'ENOENT') {
        return false;
      }
      throw error;
    }
  }

  /**
   * Removes the file a document (or one of its versions) points at. A file that
   * is already gone counts as removed, so a retried purge succeeds.
   */
  async deleteObject(document: Document): Promise<void> {
    const localPath = this.getLocalPathFromUrl(
      document.local?.localPath ?? document.local?.url,
    );
    if (localPath === null || localPath === '') {
      throw new StorageNotFoundError('Local file path not found');
    }
    const fullPath = this.assertInsideMount(
      path.join(this.mountPath, localPath),
    );
    const located = await this.locateInsideRealMount(fullPath);
    if (located === null) {
      return;
    }
    try {
      await fs.unlink(located.target);
    } catch (error) {
      if ((error as { code?: string }).code !== 'ENOENT') {
        throw error;
      }
    }
    await this.removeEmptyFoldersAbove(located.target, located.mountRoot);
  }

  /**
   * Where a file really is once symlinked folders are followed, which must still
   * be inside the mount (itself followed, since the mount may be reached through
   * a link). Null when its folder is already gone.
   */
  private async locateInsideRealMount(
    fullPath: string,
  ): Promise<{ target: string; mountRoot: string } | null> {
    let folder: string;
    try {
      folder = await fs.realpath(path.dirname(fullPath));
    } catch (error) {
      if ((error as { code?: string }).code === 'ENOENT') {
        return null;
      }
      throw error;
    }
    const mountRoot = await fs.realpath(this.mountPath);
    const target = path.join(folder, path.basename(fullPath));
    if (!target.startsWith(mountRoot + path.sep)) {
      throw new StorageValidationError('Invalid document path');
    }
    return { target, mountRoot };
  }

  private async removeEmptyFoldersAbove(
    filePath: string,
    mountRoot: string,
  ): Promise<void> {
    let folder = path.dirname(filePath);
    while (folder.startsWith(mountRoot + path.sep)) {
      try {
        await fs.rmdir(folder);
      } catch {
        // Not empty, or already gone: nothing above it can be empty either.
        return;
      }
      folder = path.dirname(folder);
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
      const fullPath = this.assertInsideMount(
        path.join(this.mountPath, relativePath),
      );
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

      await this.writeFileAtomically(fullPath, documentInPayload.buffer);

      const fileUrl = this.getFileUrl(relativePath);

      this.logger.debug('Local storage upload successful');

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
      const localPath = this.getLocalPathFromUrl(document.local?.localPath || document.local?.url);
      if (!localPath) {
        throw new StorageNotFoundError('Local file path not found');
      }

      const fullPath = this.assertInsideMount(
        path.join(this.mountPath, localPath),
      );

      await this.writeFileAtomically(fullPath, bufferDataInPayLoad);

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
      // Try localPath first, then fall back to url (for backwards compatibility)
      let fileUrl =
        version === undefined
          ? (document.local?.localPath || document.local?.url)
          : (document.versionHistory?.[version]?.local?.localPath ||
             document.versionHistory?.[version]?.local?.url);
      if (!fileUrl) {
        throw new StorageNotFoundError(
          'File URL not found for requested version',
        );
      }

      const localPath = this.getLocalPathFromUrl(fileUrl);
      if (!localPath) {
        throw new StorageNotFoundError('Invalid file URL format');
      }

      const fullPath = this.assertInsideMount(
        path.join(this.mountPath, localPath),
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
      this.assertInsideMount(path.join(this.mountPath, relativePath));
      const fileUrl = this.getFileUrl(relativePath);
      return {
        statusCode: 200,
        data: { url: fileUrl },
      };
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new PresignedUrlError(
        'Failed to generate direct upload URL for local storage',
        {
          originalError:
            error instanceof Error ? error.message : 'Unknown error',
        },
      );
    }
  }

  async writeObject(storageKey: string, data: Buffer): Promise<void> {
    try {
      const fullPath = this.objectPathInMount(storageKey);
      await fs.mkdir(path.dirname(fullPath), { recursive: true });
      // A symlinked folder on the way must not carry the write out of the mount.
      const located = await this.locateInsideRealMount(fullPath);
      if (located === null) {
        throw new StorageNotFoundError('Local folder not found');
      }
      await this.writeFileAtomically(located.target, data);
    } catch (error) {
      if (error instanceof StorageError) throw error;
      throw new StorageUploadError('Failed to write object to local storage', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * Opens a storage key for reading, or null when it does not exist. The file
   * is served from this handle, never reopened by path: a final symlink is not
   * followed (O_NOFOLLOW), and the open file must be the one at the key's real
   * place inside the mount once open, so a path swapped between the check and
   * the read cannot reach a file outside it.
   */
  async openObject(
    storageKey: string,
  ): Promise<{ handle: FileHandle; size: number } | null> {
    const located = await this.locateInsideRealMount(
      this.objectPathInMount(storageKey),
    );
    if (located === null) {
      return null;
    }
    let handle: FileHandle;
    try {
      handle = await fs.open(
        located.target,
        // Undefined on Windows, where it reads as 0 in the bitwise or.
        fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW,
      );
    } catch (error) {
      const code = (error as { code?: string }).code;
      if (code === 'ENOENT') {
        return null;
      }
      if (code === 'ELOOP') {
        throw new StorageValidationError('Invalid document path');
      }
      throw error;
    }
    try {
      const [opened, atPath, relocated] = await Promise.all([
        handle.stat(),
        fs.lstat(located.target),
        this.locateInsideRealMount(located.target),
      ]);
      if (
        !opened.isFile() ||
        relocated?.target !== located.target ||
        opened.ino !== atPath.ino ||
        opened.dev !== atPath.dev
      ) {
        throw new StorageValidationError('Invalid document path');
      }
      return { handle, size: opened.size };
    } catch (error) {
      await handle.close();
      throw error;
    }
  }

  private objectPathInMount(storageKey: string): string {
    return this.assertInsideMount(
      path.join(this.mountPath, this.sanitizePath(storageKey)),
    );
  }

  async deleteTree(storagePath: string): Promise<StorageServiceResponse<void>> {
    try {
      const relativePath = this.sanitizePath(storagePath);
      const fullPath = path.join(this.mountPath, relativePath);
      this.assertInsideMount(fullPath);
      await fs.rm(fullPath, { recursive: true, force: true });
      await this.pruneEmptyAncestors(relativePath);
      this.logger.info('Local storage delete successful', { path: relativePath });
      return { statusCode: 200, data: undefined };
    } catch (error) {
      if (error instanceof StorageError) throw error;
      throw new StorageUploadError('Failed to delete object from local storage', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async copyObject(
    sourcePath: string,
    destinationPath: string,
  ): Promise<StorageServiceResponse<string>> {
    try {
      const srcRelative = this.sanitizePath(sourcePath);
      const dstRelative = this.sanitizePath(destinationPath);
      const srcFull = path.join(this.mountPath, srcRelative);
      const dstFull = path.join(this.mountPath, dstRelative);
      await fs.mkdir(path.dirname(dstFull), { recursive: true });
      await fs.copyFile(srcFull, dstFull);
      const destUrl = this.getFileUrl(dstRelative);
      this.logger.info('Local storage copy successful', { src: srcRelative, dst: dstRelative });
      return { statusCode: 200, data: destUrl };
    } catch (error) {
      if (error instanceof StorageError) throw error;
      throw new StorageUploadError('Failed to copy object in local storage', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async renameObject(
    sourcePath: string,
    destinationPath: string,
  ): Promise<StorageServiceResponse<string>> {
    try {
      const srcRelative = this.sanitizePath(sourcePath);
      const dstRelative = this.sanitizePath(destinationPath);
      const srcFull = path.join(this.mountPath, srcRelative);
      const dstFull = path.join(this.mountPath, dstRelative);
      await fs.mkdir(path.dirname(dstFull), { recursive: true });
      try {
        await fs.rename(srcFull, dstFull);
      } catch (renameError: any) {
        const code = renameError?.code;
        if (code === 'EXDEV') {
          await fs.copyFile(srcFull, dstFull);
          await fs.rm(srcFull, { force: true });
        } else if (code === 'ENOTEMPTY' || code === 'EPERM' || code === 'EEXIST') {
          await fs.copyFile(srcFull, dstFull);
          await fs.rm(srcFull, { force: true });
        } else {
          throw renameError;
        }
      }
      await this.pruneEmptyAncestors(srcRelative);
      const destUrl = this.getFileUrl(dstRelative);
      this.logger.info('Local storage rename successful', { src: srcRelative, dst: dstRelative });
      return { statusCode: 200, data: destUrl };
    } catch (error) {
      if (error instanceof StorageError) throw error;
      throw new StorageUploadError('Failed to rename object in local storage', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async copyTree(
    sourcePrefix: string,
    destinationPrefix: string,
  ): Promise<StorageServiceResponse<void>> {
    try {
      const srcRelative = this.sanitizePath(sourcePrefix);
      const dstRelative = this.sanitizePath(destinationPrefix);
      const srcFull = path.join(this.mountPath, srcRelative);
      const dstFull = path.join(this.mountPath, dstRelative);
      await fs.mkdir(path.dirname(dstFull), { recursive: true });
      await fs.cp(srcFull, dstFull, { recursive: true });
      this.logger.info('Local storage tree copy successful', { src: srcRelative, dst: dstRelative });
      return { statusCode: 200, data: undefined };
    } catch (error) {
      // A missing source tree means there was nothing to copy - treat as a no-op success.
      if ((error as NodeJS.ErrnoException)?.code === 'ENOENT') {
        this.logger.info('Local storage tree copy skipped: source prefix does not exist', {
          src: sourcePrefix,
        });
        return { statusCode: 200, data: undefined };
      }
      if (error instanceof StorageError) throw error;
      throw new StorageUploadError('Failed to copy tree in local storage', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async renameTree(
    sourcePrefix: string,
    destinationPrefix: string,
  ): Promise<StorageServiceResponse<void>> {
    try {
      const srcRelative = this.sanitizePath(sourcePrefix);
      const dstRelative = this.sanitizePath(destinationPrefix);
      const srcFull = path.join(this.mountPath, srcRelative);
      const dstFull = path.join(this.mountPath, dstRelative);
      await fs.mkdir(path.dirname(dstFull), { recursive: true });
      try {
        await fs.rename(srcFull, dstFull);
      } catch (renameError: any) {
        const code = renameError?.code;
        if (code === 'EXDEV' || code === 'ENOTEMPTY' || code === 'EPERM' || code === 'EEXIST') {
          // EXDEV: cross-device — can't rename, must copy.
          // ENOTEMPTY/EPERM/EEXIST: destination already exists (race
          // between indexer creating docs at the new path and a
          // concurrent connector move). Merge source into destination
          // and remove the source.
          await fs.cp(srcFull, dstFull, { recursive: true });
          await fs.rm(srcFull, { recursive: true, force: true });
        } else {
          throw renameError;
        }
      }
      await this.pruneEmptyAncestors(srcRelative);
      this.logger.info('Local storage tree rename successful', { src: srcRelative, dst: dstRelative });
      return { statusCode: 200, data: undefined };
    } catch (error) {
      if ((error as NodeJS.ErrnoException)?.code === 'ENOENT') {
        // The caller decides whether a missing tree is fine; a silent success
        // here let rows be pointed at files that were never there.
        throw new StorageNotFoundError('Source prefix does not exist in local storage', {
          src: sourcePrefix,
        });
      }
      if (error instanceof StorageError) throw error;
      throw new StorageUploadError('Failed to rename tree in local storage', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * Where each listed document's folder (`<documentPath>/<documentId>`) now
   * sits under *prefix*, as documentId -> documentPath. Symlinks are not
   * followed and a document's own folder is not searched further.
   */
  async locateDocuments(
    prefix: string,
    documentIds: string[],
  ): Promise<Map<string, string>> {
    const wanted = new Set(documentIds);
    const found = new Map<string, string>();
    const walk = async (relative: string): Promise<void> => {
      let entries;
      try {
        entries = await fs.readdir(path.join(this.mountPath, relative), {
          withFileTypes: true,
        });
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === 'ENOENT') return;
        throw error;
      }
      for (const entry of entries) {
        if (found.size === wanted.size) return;
        if (!entry.isDirectory()) continue;
        if (wanted.has(entry.name)) {
          found.set(entry.name, relative.split(path.sep).join('/'));
          continue;
        }
        await walk(path.join(relative, entry.name));
      }
    };
    await walk(this.sanitizePath(prefix));
    return found;
  }

  /**
   * Walk upward from the given relative path, removing each directory
   * that is empty, stopping at the first non-empty ancestor or at the
   * mount root. Safe to call on any path — non-empty or non-existent
   * directories are silently skipped.
   */
  private async pruneEmptyAncestors(relativePath: string): Promise<void> {
    let current = path.dirname(relativePath);
    while (current && current !== '.' && current !== '/') {
      const full = path.join(this.mountPath, current);
      try {
        const entries = await fs.readdir(full);
        if (entries.length > 0) break;
        await fs.rmdir(full);
      } catch {
        break;
      }
      current = path.dirname(current);
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

  getObjectUrl(storageKey: string): string {
    return this.getFileUrl(this.sanitizePath(storageKey));
  }

  private getFileUrl(filePath: string): string {
    // Build the full local filesystem path
    const fullPath = path.join(this.mountPath, filePath);

    // Convert backslashes to forward slashes for URL format
    const urlPath = fullPath.replace(/\\/g, '/');

    // Encode each path component to handle #, ?, %, spaces, etc.
    // On Windows, don't encode the drive letter (first component)
    const encodedPath = urlPath
      .split('/')
      .map((component, index) =>
        process.platform === 'win32' && index === 0
          ? component
          : encodeURIComponent(component),
      )
      .join('/');

    if (process.platform === 'win32') {
      // Windows paths: file:///C:/encoded/path
      return `file:///${encodedPath}`;
    }

    // Unix-like systems: file:///encoded/path
    return `file://${encodedPath}`;
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

      // Get the relative path by removing the mount path
      const relativePath = path.relative(this.mountPath, localPath);

      return relativePath;
    } catch {
      return null;
    }
  }

  /**
   * Confirm a resolved path stays inside the mount. Does not follow
   * symlinks (no realpath); a symlink placed under the mount can still
   * escape — that is out of scope.
   */
  private assertInsideMount(fullPath: string): string {
    const mountRoot = path.resolve(this.mountPath);
    const resolved = path.resolve(fullPath);
    const prefix = mountRoot.endsWith(path.sep)
      ? mountRoot
      : `${mountRoot}${path.sep}`;
    if (resolved === mountRoot || !resolved.startsWith(prefix)) {
      throw new StorageValidationError('Invalid document path');
    }
    return resolved;
  }

  private sanitizePath(filePath: string): string {
    if (!filePath || filePath.includes('\0')) {
      throw new StorageValidationError('Invalid document path');
    }

    let unified = filePath.split('\\').join('/');
    while (unified.endsWith('/')) {
      unified = unified.slice(0, -1);
    }
    if (
      !unified ||
      unified === '.' ||
      unified === './' ||
      path.isAbsolute(unified) ||
      /^[a-zA-Z]:/.test(unified)
    ) {
      throw new StorageValidationError('Invalid document path');
    }

    if (unified.split('/').some((segment) => segment === '..' || segment === '.')) {
      throw new StorageValidationError('Invalid document path');
    }

    const normalized = path.normalize(
      unified.split('/').map(fitPathSegment).join('/'),
    );
    const resolved = this.assertInsideMount(
      path.join(this.mountPath, normalized),
    );
    return path.relative(path.resolve(this.mountPath), resolved);
  }
}

export default LocalStorageAdapter;