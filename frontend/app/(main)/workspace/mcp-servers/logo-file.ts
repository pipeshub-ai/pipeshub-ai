/**
 * Turns a picked image into the logo sent to the server: a PNG drawn on a canvas, at most
 * `MCP_LOGO_SIDE_PX` a side. The server accepts only raster images, so an SVG never leaves the
 * browser as SVG.
 */

export const MCP_LOGO_ACCEPT = 'image/png,image/jpeg,image/webp,image/gif,image/svg+xml';
export const MCP_LOGO_MAX_FILE_BYTES = 2 * 1024 * 1024;
/** The server's size too: the largest place a logo shows is 28 px, and 96 stays sharp on 3x screens. */
export const MCP_LOGO_SIDE_PX = 96;

const ACCEPTED_TYPES = new Set(MCP_LOGO_ACCEPT.split(','));

export type McpLogoFileProblem = 'type' | 'size' | 'unreadable';

export class McpLogoFileError extends Error {
  constructor(readonly problem: McpLogoFileProblem) {
    super(`Logo file not usable: ${problem}`);
    this.name = 'McpLogoFileError';
  }
}

/** `width` × `height` scaled down to fit a `max` square, keeping its shape; never scaled up. */
export function fitWithin(width: number, height: number, max: number): { width: number; height: number } {
  // An SVG with no size of its own reports 0 × 0.
  if (!(width > 0) || !(height > 0)) return { width: max, height: max };
  const scale = Math.min(1, max / Math.max(width, height));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

export async function imageFileToLogo(file: File): Promise<string> {
  if (!ACCEPTED_TYPES.has(file.type)) throw new McpLogoFileError('type');
  if (file.size > MCP_LOGO_MAX_FILE_BYTES) throw new McpLogoFileError('size');
  const url = URL.createObjectURL(file);
  try {
    const image = await loadImage(url);
    const { width, height } = fitWithin(image.naturalWidth, image.naturalHeight, MCP_LOGO_SIDE_PX);
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext('2d');
    if (!context) throw new McpLogoFileError('unreadable');
    context.imageSmoothingQuality = 'high';
    context.drawImage(image, 0, 0, width, height);
    return canvas.toDataURL('image/png');
  } finally {
    URL.revokeObjectURL(url);
  }
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new McpLogoFileError('unreadable'));
    image.src = src;
  });
}
