import axios from 'axios';

import { BadRequestError } from '../../../libs/errors/http.errors';
import { FileBufferInfo } from '../../../libs/middlewares/file_processor/fp.interface';
import {
  extensionToMimeType,
  getMimeType,
} from '../../storage/mimetypes/mimetypes';
import { KB_UPLOAD_LIMITS } from '../constants/kb.constants';

const MAX = KB_UPLOAD_LIMITS.defaultMaxFileSizeBytes;
const ALLOWED = new Set(Object.values(extensionToMimeType));

/** Minimal remote fetch → {@link FileBufferInfo} for the normal KB upload handler. */
export async function fetchUrlBytesForKb(
  rawUrl: string,
  preferredName?: string,
): Promise<FileBufferInfo> {
  let u: URL;
  try {
    u = new URL(rawUrl.trim());
  } catch {
    throw new BadRequestError('Invalid URL');
  }

  // TODO: Allow http in dev mode
  if (u.protocol !== 'http:' && u.protocol !== 'https:') {
    throw new BadRequestError('Only http(s) URLs');
  }

  try {
    const res = await axios.get<ArrayBuffer>(u.href, {
      responseType: 'arraybuffer',
      timeout: 30_000,
      maxContentLength: MAX + 1,
      maxRedirects: 5,
      validateStatus: (s) => s === 200,
    });

    const buffer = Buffer.from(res.data);
    if (buffer.length > MAX) throw new BadRequestError('File too large');

    const fallback = u.pathname.split('/').pop() || 'file';
    const originalname =
      (preferredName || fallback).replace(/[/\\]/g, '').slice(0, 255) || 'file';
    const ext = originalname.includes('.')
      ? originalname.slice(originalname.lastIndexOf('.') + 1).toLowerCase()
      : '';
    const ct = res.headers['content-type']?.split(';')[0]?.trim().toLowerCase();
    const mimetype =
      (ext && getMimeType(ext)) ||
      (ct && ALLOWED.has(ct) ? ct : '') ||
      '';

    if (!mimetype || !ALLOWED.has(mimetype)) {
      throw new BadRequestError('Unsupported or unknown file type');
    }

    const now = Date.now();
    return {
      buffer,
      mimetype,
      originalname,
      size: buffer.length,
      lastModified: now,
      filePath: originalname,
    };
  } catch (e) {
    if (e instanceof BadRequestError) throw e;
    if (axios.isAxiosError(e)) {
      throw new BadRequestError(e.message || 'Failed to fetch URL');
    }
    throw e;
  }
}
