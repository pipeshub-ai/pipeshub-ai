import { expect } from 'chai';
import { createZipBuffer } from '../../../src/modules/support/support.zip';
import { readZip } from './zip-reader';

describe('createZipBuffer', () => {
  it('writes every file, bytes intact, under its own name', async () => {
    const big = Buffer.alloc(100_000, 'abc');
    const files = readZip(
      await createZipBuffer([
        { name: 'shot.png', data: Buffer.from('png-bytes') },
        { name: 'app.log', data: big },
        { name: '日本語.txt', data: Buffer.from('こんにちは') },
      ]),
    );

    expect(Object.keys(files)).to.deep.equal([
      'shot.png',
      'app.log',
      '日本語.txt',
    ]);
    expect(String(files['shot.png'])).to.equal('png-bytes');
    expect(files['app.log']?.equals(big)).to.equal(true);
    expect(String(files['日本語.txt'])).to.equal('こんにちは');
  });

  it('keeps two files with the same name instead of letting one overwrite the other', async () => {
    const files = readZip(
      await createZipBuffer([
        { name: 'image.png', data: Buffer.from('one') },
        { name: 'IMAGE.png', data: Buffer.from('two') },
      ]),
    );

    expect(Object.keys(files)).to.deep.equal(['image.png', 'IMAGE (2).png']);
  });

  it('flattens a path in a name so it cannot extract outside its folder', async () => {
    const files = readZip(
      await createZipBuffer([
        { name: '../../etc/passwd', data: Buffer.from('x') },
        { name: '', data: Buffer.from('y') },
      ]),
    );

    expect(Object.keys(files)).to.deep.equal([
      '_.._etc_passwd',
      'attachment-2',
    ]);
  });
});
