import 'reflect-metadata';
import { expect } from 'chai';
import sharp from 'sharp';
import { validateAttachmentFile } from '../../../../src/modules/enterprise_search/controller/es_controller';
import { BadRequestError } from '../../../../src/libs/errors/http.errors';

const SVG = Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"><rect width="4" height="4"/></svg>',
);

async function pngBuffer(): Promise<Buffer> {
  return sharp({
    create: {
      width: 8,
      height: 8,
      channels: 3,
      background: { r: 0, g: 128, b: 255 },
    },
  })
    .png()
    .toBuffer();
}

function file(
  overrides: Partial<Express.Multer.File> & Pick<Express.Multer.File, 'buffer' | 'mimetype'>,
): Express.Multer.File {
  return {
    fieldname: 'files',
    originalname: 'photo.png',
    encoding: '7bit',
    size: overrides.buffer.length,
    ...overrides,
  } as Express.Multer.File;
}

describe('validateAttachmentFile', () => {
  it('rejects SVG bytes labelled as PNG, small or large', async () => {
    for (const size of [SVG.length, 2 * 1024 * 1024]) {
      try {
        await validateAttachmentFile(
          file({
            buffer: SVG,
            mimetype: 'image/png',
            originalname: 'photo.png',
            size,
          }),
        );
        expect.fail('mislabelled SVG should be rejected');
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError);
        expect((error as Error).message).to.equal(
          'photo.png is not a valid JPEG, PNG or WebP image',
        );
      }
    }
  });

  it('rejects a PNG signature and a PNG whose pixels do not decode', async () => {
    const signature = Buffer.from([
      0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
    ]);
    const corrupt = Buffer.from(await pngBuffer());
    corrupt[Math.floor(corrupt.length * 0.7)] ^= 0xff;

    for (const buffer of [signature, corrupt]) {
      try {
        await validateAttachmentFile(
          file({
            buffer,
            mimetype: 'image/png',
            originalname: 'photo.png',
            size: buffer.length,
          }),
        );
        expect.fail('undecodable PNG should be rejected');
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError);
        expect((error as Error).message).to.equal(
          'photo.png is not a valid JPEG, PNG or WebP image',
        );
      }
    }
  });

  it('passes a PDF through without decoding it', async () => {
    const pdf = Buffer.from('%PDF-1.4');
    const result = await validateAttachmentFile(
      file({
        buffer: pdf,
        mimetype: 'application/pdf',
        originalname: 'notes.pdf',
        size: pdf.length,
      }),
    );
    expect(result.buffer.equals(pdf)).to.equal(true);
    expect(result.mimeType).to.equal('application/pdf');
  });

  it('leaves a small real PNG unchanged', async () => {
    const png = await pngBuffer();
    const result = await validateAttachmentFile(
      file({ buffer: png, mimetype: 'image/png', size: png.length }),
    );
    expect(result.buffer.equals(png)).to.equal(true);
    expect(result.mimeType).to.equal('image/png');
  });

  it('forwards a large real PNG as uploaded: same bytes, type and name', async () => {
    const png = await pngBuffer();
    const result = await validateAttachmentFile(
      file({
        buffer: png,
        mimetype: 'image/png',
        originalname: 'large.png',
        size: 2 * 1024 * 1024,
      }),
    );
    expect(result.buffer.equals(png)).to.equal(true);
    expect(result.mimeType).to.equal('image/png');
    expect(result.fileName).to.equal('large.png');
    expect(result.size).to.equal(2 * 1024 * 1024);
  });

  it('stores the type the bytes are when the label is wrong, keeping the name and bytes', async () => {
    const png = await pngBuffer();
    const jpeg = await sharp(png).jpeg().toBuffer();
    const cases = [
      [png, 'image/jpeg', 'image/png'],
      [jpeg, 'image/png', 'image/jpeg'],
    ] as const;
    for (const [buffer, label, actual] of cases) {
      const result = await validateAttachmentFile(
        file({ buffer, mimetype: label, originalname: 'shot.jpg' }),
      );
      expect(result.mimeType).to.equal(actual);
      expect(result.fileName).to.equal('shot.jpg');
      expect(result.buffer.equals(buffer)).to.equal(true);
    }
  });

  it('keeps the label on WebP bytes, since WebP is not an attachment type', async () => {
    const webp = await sharp(await pngBuffer()).webp().toBuffer();
    const result = await validateAttachmentFile(
      file({ buffer: webp, mimetype: 'image/png' }),
    );
    expect(result.mimeType).to.equal('image/png');
  });

  it('rejects an image over 50 megapixels as too large', async () => {
    const huge = await sharp({
      create: { width: 10000, height: 6000, channels: 3, background: { r: 0, g: 0, b: 0 } },
    })
      .png()
      .toBuffer();
    try {
      await validateAttachmentFile(
        file({ buffer: huge, mimetype: 'image/png', originalname: 'huge.png' }),
      );
      expect.fail('an image over the pixel cap should be rejected');
    } catch (error) {
      expect(error).to.be.instanceOf(BadRequestError);
      expect((error as Error).message).to.equal(
        'huge.png is too large to attach: images can be at most 50 megapixels',
      );
    }
  });
});
