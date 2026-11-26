/**
 * P3.2 NW-5, NW-6: Node's workspace rules equal Python's on the shared
 * vectors (contracts/harness/vectors/workspace.json), plus the merge planner.
 */
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import {
  applyChanges,
  changedPaths,
  compareCodePoints,
  FILE_MODE,
  manifestContentHash,
  normalizeWorkspacePath,
  planMerge,
  repositoryOf,
  validateChanges,
  validFileMode,
  WorkspaceError,
  WorkspaceFileEntry,
  workspaceInSession,
} from '../../../src/modules/agent-harness/domain/workspace';

interface Vectors {
  paths: { accepted: Array<[string, string]>; refused: string[] };
  manifests: Array<{ files: WorkspaceFileEntry[]; contentHash: string }>;
}

const vectors = JSON.parse(
  readFileSync(
    path.resolve(
      __dirname,
      '../../../../../../contracts/harness/vectors/workspace.json',
    ),
    'utf8',
  ),
) as Vectors;

const file = (
  p: string,
  text: string,
  mimeType = 'text/plain',
): WorkspaceFileEntry => ({
  path: p,
  objectId: `obj-${text}`,
  contentHash: `sha256:${text}`,
  sizeBytes: text.length,
  mimeType,
});

const files = (
  ...entries: WorkspaceFileEntry[]
): Map<string, WorkspaceFileEntry> =>
  new Map(entries.map((entry) => [entry.path, entry]));

const code = (work: () => unknown): string => {
  try {
    work();
  } catch (error) {
    if (error instanceof WorkspaceError) return error.code;
    throw error;
  }
  throw new Error('expected a WorkspaceError');
};

describe('P3.2 workspace domain', () => {
  it('NW-6: accepts and canonicalises the same paths as Python', () => {
    for (const [input, canonical] of vectors.paths.accepted) {
      expect(normalizeWorkspacePath(input), JSON.stringify(input)).to.equal(
        canonical,
      );
    }
  });

  it('NW-6: refuses the same paths as Python (traversal, absolute, non-canonical, too deep or long)', () => {
    expect(vectors.paths.refused).to.include.members(['../x', 'a/../b', '/a']);
    for (const input of vectors.paths.refused) {
      expect(
        code(() => normalizeWorkspacePath(input)),
        JSON.stringify(input),
      ).to.equal('WORKSPACE_INVALID_CHANGE');
    }
    expect(code(() => normalizeWorkspacePath('a\uD800b'))).to.equal(
      'WORKSPACE_INVALID_CHANGE',
    );
  });

  it('NW-5: the manifest content hash equals Python’s, in code point order', () => {
    expect(vectors.manifests.length).to.be.greaterThan(3);
    for (const manifest of vectors.manifests) {
      const map = files(...manifest.files);
      expect(manifestContentHash(map)).to.equal(manifest.contentHash);
    }
    expect(compareCodePoints('�', '\u{1F600}')).to.equal(-1);
    expect('�' < '\u{1F600}').to.equal(false);
  });

  it('P5.5 MD-1..MD-3: file modes are validated, stored only when not the default, and part of a change', () => {
    for (const mode of [0o100644, 0o100755, 0o100600, 0o100000, 0o120777]) {
      expect(validFileMode(mode), mode.toString(8)).to.equal(true);
    }
    for (const mode of [
      0o104755, 0o102755, 0o101755, 0o040755, 0o120755, 0o644, -1, 1.5,
    ]) {
      expect(validFileMode(mode), mode.toString(8)).to.equal(false);
      expect(
        code(() =>
          validateChanges({
            upserts: [{ ...file('a', '1'), mode }],
            deletes: [],
          }),
        ),
      ).to.equal('WORKSPACE_INVALID_CHANGE');
    }
    const explicit = validateChanges({
      upserts: [{ ...file('a', '1'), mode: FILE_MODE }],
      deletes: [],
    }).upserts[0];
    expect(explicit).to.not.have.property('mode');
    const executable = { ...file('a', '1'), mode: 0o100755 };
    expect(
      changedPaths(files(file('a', '1')), files(executable)),
    ).to.deep.equal(['a']);
    expect(changedPaths(files(file('a', '1')), files(explicit!))).to.deep.equal(
      [],
    );
    expect(manifestContentHash(files(executable))).to.not.equal(
      manifestContentHash(files(file('a', '1'))),
    );
    const plan = planMerge(
      files(file('a', '1')),
      files(file('a', '1')),
      files(executable),
    );
    expect(plan.changes.upserts).to.deep.equal([executable]);
  });

  it('a change set is canonical and never writes and deletes one path', () => {
    expect(
      code(() =>
        validateChanges({ upserts: [file('a', '1')], deletes: ['a'] }),
      ),
    ).to.equal('WORKSPACE_INVALID_CHANGE');
    expect(
      code(() =>
        validateChanges({ upserts: [file('a/../b', '1')], deletes: [] }),
      ),
    ).to.equal('WORKSPACE_INVALID_CHANGE');
    expect(
      code(() => validateChanges({ upserts: [], deletes: ['café'] })),
    ).to.equal('WORKSPACE_INVALID_CHANGE');
    expect(
      code(() => applyChanges(files(), { upserts: [], deletes: ['missing'] })),
    ).to.equal('WORKSPACE_INVALID_CHANGE');
  });

  it('merges one-sided changes and reports two-sided ones', () => {
    const base = files(file('a', '1'), file('b', '1'), file('c', '1'));
    const ours = files(file('a', '2'), file('b', '1'), file('c', '1'));
    const theirs = files(file('a', '3'), file('c', '1'), file('d', '1'));
    const plan = planMerge(base, ours, theirs);
    expect(plan.conflicts.map((c) => c.path)).to.deep.equal(['a']);
    expect(plan.changes.upserts.map((e) => e.path)).to.deep.equal(['d']);
    expect(plan.changes.deletes).to.deep.equal(['b']);
    expect(plan.repositoryConflicts).to.deep.equal([]);
  });

  it('reports a repository both sides touched instead of merging its paths', () => {
    const base = files(file('.git/HEAD', 'h'), file('x/.git/HEAD', 'h'));
    const ours = files(...base.values(), file('.git/refs/heads/a', '1'));
    const theirs = files(
      ...base.values(),
      file('.git/refs/heads/b', '2'),
      file('x/.git/HEAD', 'z'),
    );
    const plan = planMerge(base, ours, theirs);
    expect(plan.repositoryConflicts).to.deep.equal(['.git']);
    expect(plan.conflicts).to.deep.equal([]);
    expect(plan.changes.upserts.map((e) => e.path)).to.deep.equal([
      'x/.git/HEAD',
    ]);
    expect(repositoryOf('src/.git/config')).to.equal('src/.git');
    expect(repositoryOf('src/.gitignore')).to.equal(null);
  });

  it('a session owns ws:{sessionId} and its forks, nothing else', () => {
    expect(workspaceInSession('ws:s1', 's1')).to.equal(true);
    expect(workspaceInSession('ws:s1:child-1', 's1')).to.equal(true);
    expect(workspaceInSession('ws:s10', 's1')).to.equal(false);
    expect(workspaceInSession('ws:s2:ws:s1', 's1')).to.equal(false);
  });
});
