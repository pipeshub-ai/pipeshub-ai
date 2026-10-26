import sharp from 'sharp';
import { BadRequestError } from '../errors/http.errors';

// libvips picks a decoder from the bytes, ignoring the MIME type the client
// sent. SVG goes through librsvg, which has had memory flaws that can run
// code on glibc Linux. This process never rasterizes SVG, so every loader
// except the four raster formats we accept stays disabled.
sharp.block({ operation: ['VipsForeignLoad'] });
sharp.unblock({
  operation: [
    'VipsForeignLoadJpeg',
    'VipsForeignLoadPng',
    'VipsForeignLoadWebp',
    'VipsForeignLoadNsgif',
  ],
});

export type RasterImageFormat = 'jpeg' | 'png' | 'webp' | 'gif';

const PNG_SIGNATURE = Buffer.from([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
]);
const JPEG_SIGNATURE = Buffer.from([0xff, 0xd8, 0xff]);
const GIF87_SIGNATURE = Buffer.from('GIF87a');
const GIF89_SIGNATURE = Buffer.from('GIF89a');
const RIFF_SIGNATURE = Buffer.from('RIFF');
const WEBP_SIGNATURE = Buffer.from('WEBP');

function startsWith(buffer: Buffer, signature: Buffer): boolean {
  return (
    buffer.length >= signature.length &&
    buffer.subarray(0, signature.length).equals(signature)
  );
}

/**
 * Identify a JPEG, PNG, WebP or GIF from its header. Anything else,
 * including SVG, returns null. The client-supplied MIME type is not read.
 */
export function detectRasterImageFormat(
  buffer: Buffer | null | undefined,
): RasterImageFormat | null {
  if (!Buffer.isBuffer(buffer)) {
    return null;
  }
  if (startsWith(buffer, PNG_SIGNATURE)) {
    return 'png';
  }
  if (startsWith(buffer, JPEG_SIGNATURE)) {
    return 'jpeg';
  }
  if (
    startsWith(buffer, GIF87_SIGNATURE) ||
    startsWith(buffer, GIF89_SIGNATURE)
  ) {
    return 'gif';
  }
  if (
    buffer.length >= 12 &&
    buffer.subarray(0, 4).equals(RIFF_SIGNATURE) &&
    buffer.subarray(8, 12).equals(WEBP_SIGNATURE)
  ) {
    return 'webp';
  }
  return null;
}

/**
 * Reject a buffer that is not one of `allowed`. Call this before any decode:
 * the loader allowlist is the backstop, this is the error the client sees.
 */
export function assertRasterImage(
  buffer: Buffer | null | undefined,
  allowed: readonly RasterImageFormat[],
  message: string,
): RasterImageFormat {
  const format = detectRasterImageFormat(buffer);
  if (format === null || !allowed.includes(format)) {
    throw new BadRequestError(message);
  }
  return format;
}

export interface UploadedImageFile {
  buffer: Buffer;
  mimetype: string | undefined;
}

/** Pull a buffer and MIME type out of a multipart body without trusting `any`. */
export function readImageUpload(body: unknown): UploadedImageFile | undefined {
  if (typeof body !== 'object' || body === null || !('fileBuffer' in body)) {
    return undefined;
  }
  const file: unknown = body.fileBuffer;
  if (typeof file !== 'object' || file === null || !('buffer' in file)) {
    return undefined;
  }
  if (!Buffer.isBuffer(file.buffer)) {
    return undefined;
  }
  const mimetype =
    'mimetype' in file && typeof file.mimetype === 'string'
      ? file.mimetype
      : undefined;
  return { buffer: file.buffer, mimetype };
}

export { sharp };
