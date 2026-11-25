import { crc32, deflateRawSync } from 'node:zlib';
import { checkPortablePath, linkEscapes } from './safe-names';
import { SYMLINK_MODE } from './workspace';

const portablePath = (path: string): boolean => {
  try {
    checkPortablePath(path);
    return true;
  } catch {
    return false;
  }
};

/**
 * A streaming ZIP writer for `/download` (SLASH3 §3.3). Node has no zip
 * library, and one is not worth adding: `zlib.crc32` and `deflateRawSync` are
 * enough. Each entry is read and compressed on its own, so the archive is
 * never held whole; sizes are known before a header is written, so no data
 * descriptors are needed. ZIP64 records are written once an offset, a size or
 * the entry count passes what the classic fields hold.
 */
export interface ZipEntry {
  path: string;
  /** The manifest mode; a symbolic link is stored as a link (its content is the target), never followed. */
  mode?: number;
  /** The file's bytes, read only when the writer reaches it. */
  read: () => Promise<Buffer>;
}

const MAX_32 = 0xffffffff;
const MAX_16 = 0xffff;
const UTF8_FLAG = 0x0800;
const UNIX = 3;
const VERSION_ZIP64 = 45;
const VERSION_DEFLATE = 20;
// 1980-01-01 00:00 in DOS time: the archive does not depend on when it was made.
const DOS_DATE = (0 << 9) | (1 << 5) | 1;
const DOS_TIME = 0;

interface Written {
  name: Buffer;
  crc: number;
  compressed: number;
  size: number;
  offset: number;
  method: number;
  externalAttributes: number;
}

function zip64Extra(fields: number[]): Buffer {
  const extra = Buffer.alloc(4 + fields.length * 8);
  extra.writeUInt16LE(0x0001, 0);
  extra.writeUInt16LE(fields.length * 8, 2);
  fields.forEach((value, i) =>
    extra.writeBigUInt64LE(BigInt(value), 4 + i * 8),
  );
  return extra;
}

function localHeader(entry: Written, zip64: boolean): Buffer {
  const extra = zip64
    ? zip64Extra([entry.size, entry.compressed])
    : Buffer.alloc(0);
  const header = Buffer.alloc(30);
  header.writeUInt32LE(0x04034b50, 0);
  header.writeUInt16LE(zip64 ? VERSION_ZIP64 : VERSION_DEFLATE, 4);
  header.writeUInt16LE(UTF8_FLAG, 6);
  header.writeUInt16LE(entry.method, 8);
  header.writeUInt16LE(DOS_TIME, 10);
  header.writeUInt16LE(DOS_DATE, 12);
  header.writeUInt32LE(entry.crc, 14);
  header.writeUInt32LE(zip64 ? MAX_32 : entry.compressed, 18);
  header.writeUInt32LE(zip64 ? MAX_32 : entry.size, 22);
  header.writeUInt16LE(entry.name.length, 26);
  header.writeUInt16LE(extra.length, 28);
  return Buffer.concat([header, entry.name, extra]);
}

function centralHeader(entry: Written, zip64: boolean): Buffer {
  const extra = zip64
    ? zip64Extra([entry.size, entry.compressed, entry.offset])
    : Buffer.alloc(0);
  const header = Buffer.alloc(46);
  header.writeUInt32LE(0x02014b50, 0);
  header.writeUInt16LE(
    (UNIX << 8) | (zip64 ? VERSION_ZIP64 : VERSION_DEFLATE),
    4,
  );
  header.writeUInt16LE(zip64 ? VERSION_ZIP64 : VERSION_DEFLATE, 6);
  header.writeUInt16LE(UTF8_FLAG, 8);
  header.writeUInt16LE(entry.method, 10);
  header.writeUInt16LE(DOS_TIME, 12);
  header.writeUInt16LE(DOS_DATE, 14);
  header.writeUInt32LE(entry.crc, 16);
  header.writeUInt32LE(zip64 ? MAX_32 : entry.compressed, 20);
  header.writeUInt32LE(zip64 ? MAX_32 : entry.size, 24);
  header.writeUInt16LE(entry.name.length, 28);
  header.writeUInt16LE(extra.length, 30);
  header.writeUInt16LE(0, 32);
  header.writeUInt16LE(0, 34);
  header.writeUInt16LE(0, 36);
  header.writeUInt32LE(entry.externalAttributes >>> 0, 38);
  header.writeUInt32LE(zip64 ? MAX_32 : entry.offset, 42);
  return Buffer.concat([header, entry.name, extra]);
}

function endRecords(
  count: number,
  size: number,
  offset: number,
  zip64: boolean,
): Buffer {
  const parts: Buffer[] = [];
  if (zip64) {
    const record = Buffer.alloc(56);
    record.writeUInt32LE(0x06064b50, 0);
    record.writeBigUInt64LE(BigInt(44), 4);
    record.writeUInt16LE((UNIX << 8) | VERSION_ZIP64, 12);
    record.writeUInt16LE(VERSION_ZIP64, 14);
    record.writeUInt32LE(0, 16);
    record.writeUInt32LE(0, 20);
    record.writeBigUInt64LE(BigInt(count), 24);
    record.writeBigUInt64LE(BigInt(count), 32);
    record.writeBigUInt64LE(BigInt(size), 40);
    record.writeBigUInt64LE(BigInt(offset), 48);
    const locator = Buffer.alloc(20);
    locator.writeUInt32LE(0x07064b50, 0);
    locator.writeUInt32LE(0, 4);
    locator.writeBigUInt64LE(BigInt(offset + size), 8);
    locator.writeUInt32LE(1, 16);
    parts.push(record, locator);
  }
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(zip64 ? MAX_16 : count, 8);
  end.writeUInt16LE(zip64 ? MAX_16 : count, 10);
  end.writeUInt32LE(zip64 ? MAX_32 : size, 12);
  end.writeUInt32LE(zip64 ? MAX_32 : offset, 16);
  parts.push(end);
  return Buffer.concat(parts);
}

/**
 * The archive's bytes, entry by entry. `forceZip64` writes the ZIP64 form at
 * any size (tests; a reader must accept both).
 */
export async function* zipStream(
  entries: Iterable<ZipEntry> | AsyncIterable<ZipEntry>,
  options: { forceZip64?: boolean } = {},
): AsyncGenerator<Buffer> {
  const written: Written[] = [];
  let offset = 0;
  // SEC-FS-02: a list's links are known before its first entry; a stream's are the ones it has shown so far, so a
  // streaming source judges each link against its whole tree before it yields it.
  const all = Symbol.iterator in entries ? [...entries] : undefined;
  const links = new Set(
    (all ?? [])
      .filter((entry) => entry.mode === SYMLINK_MODE)
      .map((entry) => entry.path),
  );
  for await (const entry of all ?? entries) {
    if (!portablePath(entry.path)) continue;
    const content = await entry.read();
    const link = entry.mode === SYMLINK_MODE;
    // SEC-FS-02: a link an unzip on any system would resolve outside the folder is left out, never shipped.
    if (link && linkEscapes(entry.path, content.toString('utf8'), links))
      continue;
    if (link) links.add(entry.path);
    // A link's target is a few bytes; deflate would only grow it.
    const deflated = link ? content : deflateRawSync(content);
    const stored = link || deflated.length >= content.length;
    const record: Written = {
      name: Buffer.from(entry.path, 'utf8'),
      crc: crc32(content),
      compressed: stored ? content.length : deflated.length,
      size: content.length,
      offset,
      method: stored ? 0 : 8,
      externalAttributes: ((entry.mode ?? 0o100644) & 0xffff) << 16,
    };
    const zip64 =
      options.forceZip64 === true ||
      record.size >= MAX_32 ||
      record.compressed >= MAX_32;
    const header = localHeader(record, zip64);
    yield header;
    yield stored ? content : deflated;
    offset += header.length + record.compressed;
    written.push(record);
  }
  const centralStart = offset;
  let centralSize = 0;
  let anyZip64 = options.forceZip64 === true || written.length >= MAX_16;
  for (const record of written) {
    const zip64 =
      options.forceZip64 === true ||
      record.size >= MAX_32 ||
      record.compressed >= MAX_32 ||
      record.offset >= MAX_32;
    anyZip64 ||= zip64;
    const header = centralHeader(record, zip64);
    centralSize += header.length;
    yield header;
  }
  anyZip64 ||= centralStart >= MAX_32 || centralSize >= MAX_32;
  yield endRecords(written.length, centralSize, centralStart, anyZip64);
}
