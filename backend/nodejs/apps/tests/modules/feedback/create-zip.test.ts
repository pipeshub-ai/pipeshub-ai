/// <reference types="mocha" />
import { expect } from 'chai';
import { createZipBuffer } from '../../../src/modules/feedback/utils/create-zip';

describe('feedback/utils/create-zip', () => {
  it('builds a zip that contains each file name and payload', () => {
    const zip = createZipBuffer([
      { name: 'shot.png', data: Buffer.from('png-bytes') },
      { name: 'notes.txt', data: Buffer.from('hello') },
    ]);

    expect(zip.subarray(0, 4).equals(Buffer.from([0x50, 0x4b, 0x03, 0x04]))).to.equal(true);
    expect(zip.includes(Buffer.from('shot.png'))).to.equal(true);
    expect(zip.includes(Buffer.from('notes.txt'))).to.equal(true);
    expect(zip.includes(Buffer.from('png-bytes'))).to.equal(true);
    expect(zip.includes(Buffer.from('hello'))).to.equal(true);
  });

  it('writes a readable central directory with the file name length at offset 28', () => {
    const zip = createZipBuffer([{ name: 'shot.png', data: Buffer.from('png-bytes') }]);
    const eocdOffset = zip.length - 22;
    expect(zip.readUInt32LE(eocdOffset)).to.equal(0x06054b50);
    expect(zip.readUInt16LE(eocdOffset + 8)).to.equal(1);
    const centralOffset = zip.readUInt32LE(eocdOffset + 16);
    expect(zip.readUInt32LE(centralOffset)).to.equal(0x02014b50);
    expect(zip.readUInt16LE(centralOffset + 28)).to.equal(Buffer.byteLength('shot.png', 'utf8'));
    expect(zip.subarray(centralOffset + 46, centralOffset + 46 + 8).toString('utf8')).to.equal(
      'shot.png',
    );
  });

  it('strips path separators from file names', () => {
    const zip = createZipBuffer([
      { name: '../secret/../shot.png', data: Buffer.from('x') },
    ]);
    expect(zip.includes(Buffer.from('../secret'))).to.equal(false);
    expect(zip.includes(Buffer.from('shot.png'))).to.equal(true);
  });
});
