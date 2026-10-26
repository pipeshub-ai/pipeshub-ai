import 'reflect-metadata';
import { expect } from 'chai';
import sharp from 'sharp';
import { BadRequestError } from '../../../src/libs/errors/http.errors';
import {
  assertRasterImage,
  detectRasterImageFormat,
  sharp as guardedSharp,
  RasterImageFormat,
} from '../../../src/libs/utils/image.utils';

async function raster(format: RasterImageFormat): Promise<Buffer> {
  const image = sharp({
    create: {
      width: 4,
      height: 4,
      channels: 3,
      background: { r: 255, g: 0, b: 0 },
    },
  });
  switch (format) {
    case 'jpeg':
      return image.jpeg().toBuffer();
    case 'png':
      return image.png().toBuffer();
    case 'webp':
      return image.webp().toBuffer();
    case 'gif':
      return image.gif().toBuffer();
  }
}

const SVG = Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"><rect width="4" height="4"/></svg>',
);
const SVG_WITH_PROLOG = Buffer.from(
  '<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"></svg>',
);

describe('image.utils', () => {
  describe('detectRasterImageFormat', () => {
    it('recognises JPEG, PNG, WebP and GIF headers', async () => {
      expect(detectRasterImageFormat(await raster('jpeg'))).to.equal('jpeg');
      expect(detectRasterImageFormat(await raster('png'))).to.equal('png');
      expect(detectRasterImageFormat(await raster('webp'))).to.equal('webp');
      expect(detectRasterImageFormat(await raster('gif'))).to.equal('gif');
    });

    it('returns null for empty, truncated, SVG and lookalike headers', () => {
      expect(detectRasterImageFormat(Buffer.alloc(0))).to.equal(null);
      expect(detectRasterImageFormat(null)).to.equal(null);
      expect(detectRasterImageFormat(undefined)).to.equal(null);
      expect(detectRasterImageFormat(Buffer.from([0x89, 0x50, 0x4e, 0x47]))).to.equal(
        null,
      );
      expect(detectRasterImageFormat(Buffer.from([0xff, 0xd8]))).to.equal(null);
      expect(detectRasterImageFormat(SVG)).to.equal(null);
      expect(detectRasterImageFormat(SVG_WITH_PROLOG)).to.equal(null);
      expect(detectRasterImageFormat(Buffer.from('RIFF\x00\x00\x00\x00WAVE'))).to.equal(
        null,
      );
      expect(detectRasterImageFormat(Buffer.from('GIF88a'))).to.equal(null);
    });
  });

  describe('assertRasterImage', () => {
    it('returns the detected format when it is allowed', async () => {
      const png = await raster('png');
      expect(assertRasterImage(png, ['png', 'jpeg'], 'nope')).to.equal('png');
    });

    it('rejects a format that is not in the allowlist', async () => {
      const gif = await raster('gif');
      expect(() => assertRasterImage(gif, ['png'], 'GIF is not allowed')).to.throw(
        BadRequestError,
        'GIF is not allowed',
      );
    });
  });

  describe('loader allowlist', () => {
    it('still decodes JPEG, PNG, WebP and GIF', async () => {
      for (const format of ['jpeg', 'png', 'webp', 'gif'] as const) {
        const buffer = await raster(format);
        const metadata = await guardedSharp(buffer).metadata();
        expect(metadata.format).to.equal(format);
        const jpeg = await guardedSharp(buffer).jpeg().toBuffer();
        expect(jpeg.subarray(0, 3).equals(Buffer.from([0xff, 0xd8, 0xff]))).to.equal(
          true,
        );
      }
    });

    it('refuses to decode SVG and TIFF', async () => {
      const tiff = await sharp({
        create: {
          width: 4,
          height: 4,
          channels: 3,
          background: { r: 0, g: 0, b: 255 },
        },
      })
        .tiff()
        .toBuffer();

      for (const buffer of [SVG, tiff]) {
        try {
          await guardedSharp(buffer).metadata();
          expect.fail('decode should have been refused');
        } catch (error) {
          expect((error as Error).message).to.include('unsupported image format');
        }
      }
    });
  });
});
