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

  it('strips path separators from file names', () => {
    const zip = createZipBuffer([
      { name: '../secret/../shot.png', data: Buffer.from('x') },
    ]);
    expect(zip.includes(Buffer.from('../secret'))).to.equal(false);
    expect(zip.includes(Buffer.from('shot.png'))).to.equal(true);
  });
});
