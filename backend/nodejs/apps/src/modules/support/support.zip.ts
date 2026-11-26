import { promisify } from 'util';
import { deflateRaw } from 'zlib';

export interface ZipEntry {
  name: string;
  data: Buffer;
}

const deflateRawAsync = promisify(deflateRaw);
const UTF8_NAMES = 0x0800;
const DEFLATE = 8;

// zlib.crc32 only exists from Node 22.2, and this build supports 22.0.
const CRC_TABLE = Uint32Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k += 1) {
    c = (c & 1) === 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  }
  return c >>> 0;
});

function crc32(data: Buffer): number {
  let c = 0xffffffff;
  for (const byte of data) c = (CRC_TABLE[(c ^ byte) & 0xff] ?? 0) ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

/** A name safe to extract: no folders, no leading dots, and no two entries alike (a second one would overwrite the first). */
function entryName(name: string, index: number, taken: Set<string>): string {
  const cleaned = name
    .replace(/[/\\]+/g, '_')
    .replace(/^\.+/, '')
    .trim();
  const base = cleaned === '' ? `attachment-${String(index + 1)}` : cleaned;
  const dot = base.lastIndexOf('.');
  const stem = dot > 0 ? base.slice(0, dot) : base;
  const extension = dot > 0 ? base.slice(dot) : '';
  let candidate = base;
  for (let copy = 2; taken.has(candidate.toLowerCase()); copy += 1) {
    candidate = `${stem} (${String(copy)})${extension}`;
  }
  taken.add(candidate.toLowerCase());
  return candidate;
}

/** One .zip of the files, deflated, with UTF-8 names. Files are already capped at a few MB, so it is built in memory. */
export async function createZipBuffer(entries: ZipEntry[]): Promise<Buffer> {
  const stamp = new Date();
  const time =
    (stamp.getHours() << 11) |
    (stamp.getMinutes() << 5) |
    (stamp.getSeconds() >> 1);
  const date =
    ((Math.max(stamp.getFullYear(), 1980) - 1980) << 9) |
    ((stamp.getMonth() + 1) << 5) |
    stamp.getDate();

  const taken = new Set<string>();
  const parts: Buffer[] = [];
  const directory: Buffer[] = [];
  let offset = 0;

  for (const [index, entry] of entries.entries()) {
    const name = Buffer.from(entryName(entry.name, index, taken), 'utf8');
    const packed = await deflateRawAsync(entry.data);
    const crc = crc32(entry.data);

    const local = Buffer.alloc(30 + name.length);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(UTF8_NAMES, 6);
    local.writeUInt16LE(DEFLATE, 8);
    local.writeUInt16LE(time, 10);
    local.writeUInt16LE(date, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(packed.length, 18);
    local.writeUInt32LE(entry.data.length, 22);
    local.writeUInt16LE(name.length, 26);
    name.copy(local, 30);

    const central = Buffer.alloc(46 + name.length);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(UTF8_NAMES, 8);
    central.writeUInt16LE(DEFLATE, 10);
    central.writeUInt16LE(time, 12);
    central.writeUInt16LE(date, 14);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(packed.length, 20);
    central.writeUInt32LE(entry.data.length, 24);
    central.writeUInt16LE(name.length, 28);
    central.writeUInt32LE(offset, 42);
    name.copy(central, 46);

    parts.push(local, packed);
    directory.push(central);
    offset += local.length + packed.length;
  }

  const directoryBytes = Buffer.concat(directory);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(directory.length, 8);
  end.writeUInt16LE(directory.length, 10);
  end.writeUInt32LE(directoryBytes.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...parts, directoryBytes, end]);
}
