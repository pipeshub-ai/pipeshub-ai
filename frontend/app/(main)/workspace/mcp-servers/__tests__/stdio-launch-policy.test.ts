import { describe, it, expect } from 'vitest';
import { findStdioLaunchIssue } from '../stdio-launch-policy';

describe('findStdioLaunchIssue', () => {
  it.each([
    ['npx', ['-y', 'pkg@1.2.3']],
    ['npx', ['-y', '--package=@scope/pkg@1.2.3', 'pkg-bin']],
    ['uvx', ['--python', '3.12', 'pkg==1.2.3']],
    ['pipx', ['run', '--spec', 'pkg==1.2.3', 'pkg-bin']],
    ['node', ['server.js']],
  ])('accepts %s %j', (command, args) => {
    expect(findStdioLaunchIssue(command, args)).toBeNull();
  });

  it.each([
    ['npx', ['-y', 'pkg'], 'pkg'],
    ['npx', ['-y', 'pkg@latest'], 'pkg@latest'],
    ['npx', ['-y', 'pkg@^1'], 'pkg@^1'],
    ['pnpm', ['dlx', '@scope/pkg'], '@scope/pkg'],
    ['uvx', ['awslabs.redshift-mcp-server@latest'], 'awslabs.redshift-mcp-server@latest'],
  ])('flags %s %j as unpinned', (command, args, spec) => {
    expect(findStdioLaunchIssue(command, args)).toMatchObject({ kind: 'unpinned', spec });
  });

  it('rejects the npx shell option and a missing package', () => {
    expect(findStdioLaunchIssue('npx', ['-c', 'id'])).toEqual({ kind: 'forbiddenOption', option: '-c' });
    expect(findStdioLaunchIssue('npx', ['-y'])).toEqual({ kind: 'missingPackage' });
  });
});
