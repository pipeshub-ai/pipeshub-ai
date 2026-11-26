/**
 * SEC-FS-02, Node's half (SFN-N1..SFN-N6): a name a person or an engine supplies never becomes a path outside its
 * folder. The shared vectors (`contracts/harness/vectors/safe-names.json`) hold here as in Python; a person's upload
 * into the workspace refuses an unsafe name plainly; the `/download` zip never carries a path or a link a reader on
 * another system would place outside its folder; a download's header names one plain file; an object id never
 * carries a path.
 */
import { bufferedUploadFile } from '../../../src/modules/agent-harness/application/session-workspace-service';
import 'reflect-metadata';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect } from 'chai';
import {
  checkName,
  checkPortablePath,
  linkEscapes,
  linkTargetEscapes,
  UnsafeNameError,
} from '../../../src/modules/agent-harness/domain/safe-names';
import { fileEntry, SYMLINK_MODE, WorkspaceError } from '../../../src/modules/agent-harness/domain/workspace';
import { zipStream, ZipEntry } from '../../../src/modules/agent-harness/domain/workspace-zip';
import { safeFileHeaders } from '../../../src/modules/agent-harness/transport/session-controller';
import { scope, workspaceWorld } from './support/slash2-workspace-world';

interface Vectors {
  names: { accepted: Array<[string, string]>; refused: Array<[string, string]> };
  paths: { accepted: Array<[string, string]>; refused: Array<[string, string]> };
  linkTargets: { plain: string[]; refused: string[] };
}

const vectors = JSON.parse(
  readFileSync(path.resolve(__dirname, '../../../../../../contracts/harness/vectors/safe-names.json'), 'utf8'),
) as Vectors;

const reasonOf = (work: () => unknown): string => {
  try {
    work();
  } catch (error) {
    expect(error).to.be.instanceOf(UnsafeNameError);
    return (error as UnsafeNameError).reason;
  }
  return 'accepted';
};

describe('SEC-FS-02: the shared name vectors (Node)', () => {
  it('SFN-N1: plain names are accepted in NFC; unsafe ones are refused with the reason Python gives', () => {
    for (const [raw, canonical] of vectors.names.accepted) expect(checkName(raw), JSON.stringify(raw)).to.equal(canonical);
    for (const [raw, reason] of vectors.names.refused) expect(reasonOf(() => checkName(raw)), JSON.stringify(raw)).to.equal(reason);
  });

  it('SFN-N2: workspace paths Linux allows are accepted; ones that escape on some system are refused', () => {
    for (const [raw, canonical] of vectors.paths.accepted) expect(checkPortablePath(raw), JSON.stringify(raw)).to.equal(canonical);
    for (const [raw, reason] of vectors.paths.refused) expect(reasonOf(() => checkPortablePath(raw)), JSON.stringify(raw)).to.equal(reason);
  });

  it('SFN-N3: a link target of another system escapes; a plain one is judged by where it lands', () => {
    for (const target of vectors.linkTargets.plain) expect(linkTargetEscapes(target), target).to.equal(false);
    for (const target of vectors.linkTargets.refused) {
      expect(linkTargetEscapes(target), JSON.stringify(target)).to.equal(true);
      expect(linkEscapes('dir/link', target)).to.equal(true);
    }
    expect(linkEscapes('a/l', '../b')).to.equal(false);
    expect(linkEscapes('a/l', '../../b')).to.equal(true);
    // Through another link: the kernel resolves that one first.
    expect(linkEscapes('x', 'd/l/../..', new Set(['d/l', 'x']))).to.equal(true);
  });
});

describe('SEC-FS-02: where a supplied name becomes a file (Node)', () => {
  const upload = (w: ReturnType<typeof workspaceWorld>, name: string, folder?: string) =>
    w.owner.upload(scope, {
      baseRevision: 1,
      idempotencyKey: `up-${name}`,
      ...(folder !== undefined ? { folder } : {}),
      files: [bufferedUploadFile(name, Buffer.from('x'), 'text/plain')],
      replace: false,
    });

  for (const [name, folder] of [
    ['CON.txt', undefined],
    ['a%2f..%2fb.txt', undefined],
    ['report.', undefined],
    ['c:evil.exe', undefined],
    ['ok.txt', 'C:'],
    ['ok.txt', 'uploads/%2e%2e'],
    ['sub/AUX', undefined],
  ] as const) {
    it(`SFN-N4: a person's upload of ${JSON.stringify(folder ?? 'uploads')}/${JSON.stringify(name)} is refused plainly and writes nothing`, async () => {
      const w = workspaceWorld();
      await w.write(w.run('run-a'), { 'a.txt': 'one\n' });
      let refused: unknown;
      try {
        await upload(w, name, folder);
      } catch (error) {
        refused = error;
      }
      expect(refused).to.be.instanceOf(WorkspaceError);
      expect((refused as WorkspaceError).code).to.equal('WORKSPACE_INVALID_CHANGE');
      expect((refused as Error).message).to.match(/^The name .* can't be used/);
      expect((await w.owner.versions(scope, {})).versions).to.have.length(1);
    });
  }

  it('SFN-N4: a plain upload, sub-folder and all, still lands', async () => {
    const w = workspaceWorld();
    await w.write(w.run('run-a'), { 'a.txt': 'one\n' });
    await upload(w, 'deck/Q3 report (final).pdf');
    const files = (await w.owner.files(scope, {})).files.map((f) => f.path);
    expect(files).to.include('uploads/deck/Q3 report (final).pdf');
  });

  const names = async (entries: ZipEntry[]): Promise<string[]> => {
    const parts: Buffer[] = [];
    for await (const chunk of zipStream(entries)) parts.push(chunk);
    const zip = Buffer.concat(parts);
    const found: string[] = [];
    // Central directory entries: signature, then the name at offset 46.
    for (let at = zip.indexOf(Buffer.from([0x50, 0x4b, 0x01, 0x02])); at >= 0; at = zip.indexOf(Buffer.from([0x50, 0x4b, 0x01, 0x02]), at + 4)) {
      const length = zip.readUInt16LE(at + 28);
      found.push(zip.subarray(at + 46, at + 46 + length).toString('utf8'));
    }
    return found;
  };
  const zipEntry = (p: string, content: string, mode?: number): ZipEntry => ({
    path: p,
    ...(mode !== undefined ? { mode } : {}),
    read: async () => Buffer.from(content),
  });

  it('SFN-N5: the zip leaves out a path with a drive or an encoded separator, and a link a reader would place outside', async () => {
    const kept = await names([
      zipEntry('ok.txt', 'ok'),
      zipEntry('C:/Windows/evil.dll', 'x'),
      zipEntry('dir/%2e%2e/x', 'x'),
      zipEntry('inside', 'ok.txt', SYMLINK_MODE),
      zipEntry('back', '..\\..\\x', SYMLINK_MODE),
      zipEntry('drive', 'C:/x', SYMLINK_MODE),
      zipEntry('up', '../x', SYMLINK_MODE),
      zipEntry('d/l', '.', SYMLINK_MODE),
      zipEntry('through', 'd/l/../..', SYMLINK_MODE),
    ]);
    expect(kept).to.deep.equal(['ok.txt', 'inside', 'd/l']);
  });

  it('R16-ZIP-1 (SFN-N5): a streamed archive leaves out the same paths and links as a listed one', async () => {
    const vectors = [
      zipEntry('ok.txt', 'ok'),
      zipEntry('C:/Windows/evil.dll', 'x'),
      zipEntry('dir/%2e%2e/x', 'x'),
      zipEntry('inside', 'ok.txt', SYMLINK_MODE),
      zipEntry('back', '..\\..\\x', SYMLINK_MODE),
      zipEntry('drive', 'C:/x', SYMLINK_MODE),
      zipEntry('up', '../x', SYMLINK_MODE),
      zipEntry('d/l', '.', SYMLINK_MODE),
      zipEntry('through', 'd/l/../..', SYMLINK_MODE),
    ];
    async function* streamed(): AsyncGenerator<ZipEntry> {
      for (const entry of vectors) yield entry;
    }
    const parts: Buffer[] = [];
    for await (const chunk of zipStream(streamed())) parts.push(chunk);
    expect(await names(vectors)).to.deep.equal(['ok.txt', 'inside', 'd/l']);
    const zip = Buffer.concat(parts);
    const found: string[] = [];
    for (let at = zip.indexOf(Buffer.from([0x50, 0x4b, 0x01, 0x02])); at >= 0; at = zip.indexOf(Buffer.from([0x50, 0x4b, 0x01, 0x02]), at + 4)) {
      found.push(zip.subarray(at + 46, at + 46 + zip.readUInt16LE(at + 28)).toString('utf8'));
    }
    expect(found).to.deep.equal(['ok.txt', 'inside', 'd/l']);
  });

  it("SFN-N6: a download's header names one plain file; the encoded form escapes every character RFC 5987 does not allow", () => {
    const headers: Record<string, string> = {};
    const res = { setHeader: (k: string, v: string) => { headers[k] = v; return res; } };
    safeFileHeaders(res as never, "dir/it's (draft)*.txt", true);
    expect(headers['Content-Disposition']).to.equal(
      `attachment; filename="it's (draft)*.txt"; filename*=UTF-8''it%27s%20%28draft%29%2A.txt`,
    );
    safeFileHeaders(res as never, 'dir/CON.txt', true);
    expect(headers['Content-Disposition']).to.equal(`attachment; filename="file_CON.txt"; filename*=UTF-8''file_CON.txt`);
  });

  it('SFN-N7: an object id with a dot segment, an escape or a control character is refused', () => {
    const entry = { path: 'a.txt', objectId: 'obj-1', contentHash: `sha256:${'a'.repeat(64)}`, sizeBytes: 1, mimeType: 'text/plain' };
    expect(fileEntry(entry).objectId).to.equal('obj-1');
    expect(fileEntry({ ...entry, objectId: `sealed/1/sha256:${'b'.repeat(64)}/doc-1` }).objectId).to.contain('doc-1');
    for (const objectId of ['..', '../x', 'sealed/1/h/..', 'a/./b', 'a\\b', '%2e%2e', 'a\u0000b', 'x'.repeat(600)]) {
      expect(() => fileEntry({ ...entry, objectId }), objectId).to.throw(WorkspaceError);
    }
  });
});
