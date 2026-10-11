import 'reflect-metadata';
import { expect } from 'chai';
import sharp from 'sharp';
import { compressImageIfNeeded } from '../../../../src/modules/enterprise_search/controller/es_controller';
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

describe('compressImageIfNeeded', () => {
  it('rejects SVG bytes labelled as PNG whether or not they exceed the size threshold', async () => {
    for (const size of [SVG.length, 2 * 1024 * 1024]) {
      try {
        await compressImageIfNeeded(
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
        await compressImageIfNeeded(
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
    const result = await compressImageIfNeeded(
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
    const result = await compressImageIfNeeded(
      file({ buffer: png, mimetype: 'image/png', size: png.length }),
    );
    expect(result.buffer.equals(png)).to.equal(true);
    expect(result.mimeType).to.equal('image/png');
  });

  it('compresses a real PNG once it is over the threshold', async () => {
    const png = await pngBuffer();
    const result = await compressImageIfNeeded(
      file({
        buffer: png,
        mimetype: 'image/png',
        originalname: 'large.png',
        size: 2 * 1024 * 1024,
      }),
    );
    expect(result.mimeType).to.equal('image/jpeg');
    expect(result.fileName).to.equal('large.jpg');
    expect(result.size).to.be.lessThan(2 * 1024 * 1024);
    expect(result.buffer.subarray(0, 3).equals(Buffer.from([0xff, 0xd8, 0xff]))).to.equal(
      true,
    );
  });
});
