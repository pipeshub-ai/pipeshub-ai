import { inflateRawSync } from 'zlib';

/** Reads back the zips support.zip.ts writes (no archive comment, no extra fields): name → bytes. */
export function readZip(zip: Buffer): Record<string, Buffer> {
  const end = zip.length - 22;
  if (zip.readUInt32LE(end) !== 0x06054b50) throw new Error('Not a zip');
  const count = zip.readUInt16LE(end + 10);
  let at = zip.readUInt32LE(end + 16);
  const files: Record<string, Buffer> = {};
  for (let i = 0; i < count; i += 1) {
    if (zip.readUInt32LE(at) !== 0x02014b50) throw new Error('Bad entry');
    const packedSize = zip.readUInt32LE(at + 20);
    const nameLength = zip.readUInt16LE(at + 28);
    const local = zip.readUInt32LE(at + 42);
    const name = zip.toString('utf8', at + 46, at + 46 + nameLength);
    const start = local + 30 + zip.readUInt16LE(local + 26);
    files[name] = inflateRawSync(zip.subarray(start, start + packedSize));
    at += 46 + nameLength;
  }
  return files;
}
