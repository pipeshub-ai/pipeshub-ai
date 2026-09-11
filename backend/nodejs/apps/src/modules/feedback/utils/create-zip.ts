import { promisify } from 'util';
import { crc32, deflateRaw } from 'zlib';

export interface ZipEntry {
  name: string;
  data: Buffer;
}

const UTF8_FLAG = 0x0800;
const DEFLATE = 8;
const deflateRawAsync = promisify(deflateRaw);

function dosDateTime(date: Date): { time: number; date: number } {
  const year = Math.max(date.getFullYear(), 1980);
  return {
    time:
      (date.getHours() << 11) |
      (date.getMinutes() << 5) |
      Math.floor(date.getSeconds() / 2),
    date:
      ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate(),
  };
}

function sanitizeZipName(name: string, index: number): string {
  const base = name.replace(/[/\\]+/g, '_').replace(/^\.+/, '').trim();
  return base.length > 0 ? base : `attachment-${index + 1}`;
}

function writeUInt16LE(target: Buffer, offset: number, value: number): void {
  target.writeUInt16LE(value, offset);
}

function writeUInt32LE(target: Buffer, offset: number, value: number): void {
  target.writeUInt32LE(value >>> 0, offset);
}

function yieldEventLoop(): Promise<void> {
  return new Promise((resolve) => setImmediate(resolve));
}

async function compressEntry(data: Buffer): Promise<{ compressed: Buffer; crc: number }> {
  const compressed = (await deflateRawAsync(data)) as Buffer;
  const crc = crc32(data) >>> 0;
  await yieldEventLoop();
  return { compressed, crc };
}

export async function createZipBuffer(entries: ZipEntry[]): Promise<Buffer> {
  const now = dosDateTime(new Date());
  const locals: Buffer[] = [];
  const centrals: Buffer[] = [];
  let offset = 0;

  for (let index = 0; index < entries.length; index += 1) {
    const entry = entries[index];
    if (!entry) {
      continue;
    }
    const name = sanitizeZipName(entry.name, index);
    const nameBuf = Buffer.from(name, 'utf8');
    const { compressed, crc } = await compressEntry(entry.data);

    const local = Buffer.alloc(30 + nameBuf.length);
    writeUInt32LE(local, 0, 0x04034b50);
    writeUInt16LE(local, 4, 20);
    writeUInt16LE(local, 6, UTF8_FLAG);
    writeUInt16LE(local, 8, DEFLATE);
    writeUInt16LE(local, 10, now.time);
    writeUInt16LE(local, 12, now.date);
    writeUInt32LE(local, 14, crc);
    writeUInt32LE(local, 18, compressed.length);
    writeUInt32LE(local, 22, entry.data.length);
    writeUInt16LE(local, 26, nameBuf.length);
    writeUInt16LE(local, 28, 0);
    nameBuf.copy(local, 30);

    const central = Buffer.alloc(46 + nameBuf.length);
    writeUInt32LE(central, 0, 0x02014b50);
    writeUInt16LE(central, 4, 20);
    writeUInt16LE(central, 6, 20);
    writeUInt16LE(central, 8, UTF8_FLAG);
    writeUInt16LE(central, 10, DEFLATE);
    writeUInt16LE(central, 12, now.time);
    writeUInt16LE(central, 14, now.date);
    writeUInt32LE(central, 16, crc);
    writeUInt32LE(central, 20, compressed.length);
    writeUInt32LE(central, 24, entry.data.length);
    writeUInt16LE(central, 28, nameBuf.length);
    writeUInt16LE(central, 30, 0);
    writeUInt16LE(central, 32, 0);
    writeUInt16LE(central, 34, 0);
    writeUInt16LE(central, 36, 0);
    writeUInt32LE(central, 38, 0);
    writeUInt32LE(central, 42, offset);
    nameBuf.copy(central, 46);

    locals.push(local, compressed);
    centrals.push(central);
    offset += local.length + compressed.length;
  }

  const centralDir = Buffer.concat(centrals);
  const eocd = Buffer.alloc(22);
  writeUInt32LE(eocd, 0, 0x06054b50);
  writeUInt16LE(eocd, 4, 0);
  writeUInt16LE(eocd, 6, 0);
  writeUInt16LE(eocd, 8, entries.length);
  writeUInt16LE(eocd, 10, entries.length);
  writeUInt32LE(eocd, 12, centralDir.length);
  writeUInt32LE(eocd, 16, offset);
  writeUInt16LE(eocd, 20, 0);

  return Buffer.concat([...locals, centralDir, eocd]);
}
