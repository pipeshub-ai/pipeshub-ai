/// <reference types="mocha" />
import { expect } from 'chai';
import { promisify } from 'util';
import { inflateRaw } from 'zlib';
import { createZipBuffer, type ZipEntry } from '../../../src/modules/feedback/utils/create-zip';

const inflateRawAsync = promisify(inflateRaw);

async function inflateFirstFile(zip: Buffer): Promise<Buffer> {
  const nameLen = zip.readUInt16LE(26);
  const extraLen = zip.readUInt16LE(28);
  const compressedSize = zip.readUInt32LE(18);
  const start = 30 + nameLen + extraLen;
  const payload = zip.subarray(start, start + compressedSize);
  return inflateRawAsync(payload) as Promise<Buffer>;
}

describe('feedback/utils/create-zip', () => {
  it('builds a deflated zip that inflates back to each payload', async () => {
    const zip = await createZipBuffer([
      { name: 'shot.png', data: Buffer.from('png-bytes') },
      { name: 'notes.txt', data: Buffer.from('hello') },
    ]);

    expect(zip.subarray(0, 4).equals(Buffer.from([0x50, 0x4b, 0x03, 0x04]))).to.equal(true);
    expect(zip.readUInt16LE(8)).to.equal(8);
    expect(zip.includes(Buffer.from('shot.png'))).to.equal(true);
    expect(zip.includes(Buffer.from('notes.txt'))).to.equal(true);
    expect((await inflateFirstFile(zip)).toString()).to.equal('png-bytes');
  });

  it('writes a readable central directory with the file name length at offset 28', async () => {
    const zip = await createZipBuffer([{ name: 'shot.png', data: Buffer.from('png-bytes') }]);
    const eocdOffset = zip.length - 22;
    expect(zip.readUInt32LE(eocdOffset)).to.equal(0x06054b50);
    expect(zip.readUInt16LE(eocdOffset + 8)).to.equal(1);
    const centralOffset = zip.readUInt32LE(eocdOffset + 16);
    expect(zip.readUInt32LE(centralOffset)).to.equal(0x02014b50);
    expect(zip.readUInt16LE(centralOffset + 10)).to.equal(8);
    expect(zip.readUInt16LE(centralOffset + 28)).to.equal(Buffer.byteLength('shot.png', 'utf8'));
    expect(zip.subarray(centralOffset + 46, centralOffset + 46 + 8).toString('utf8')).to.equal(
      'shot.png',
    );
  });

  it('strips path separators from file names', async () => {
    const zip = await createZipBuffer([
      { name: '../secret/../shot.png', data: Buffer.from('x') },
    ]);
    expect(zip.includes(Buffer.from('../secret'))).to.equal(false);
    expect(zip.includes(Buffer.from('shot.png'))).to.equal(true);
  });

  it('counts only written entries in the EOCD when the input array is sparse', async () => {
    const entries: ZipEntry[] = [];
    entries[1] = { name: 'notes.txt', data: Buffer.from('hello') };
    const zip = await createZipBuffer(entries);
    const eocdOffset = zip.length - 22;
    expect(zip.readUInt16LE(eocdOffset + 8)).to.equal(1);
    expect(zip.readUInt16LE(eocdOffset + 10)).to.equal(1);
    expect((await inflateFirstFile(zip)).toString()).to.equal('hello');
  });

  it('shrinks highly compressible text', async () => {
    const data = Buffer.from('feedback-attachment\n'.repeat(400));
    const zip = await createZipBuffer([{ name: 'notes.txt', data }]);
    expect(zip.length).to.be.lessThan(data.length);
    expect((await inflateFirstFile(zip)).equals(data)).to.equal(true);
  });
});
