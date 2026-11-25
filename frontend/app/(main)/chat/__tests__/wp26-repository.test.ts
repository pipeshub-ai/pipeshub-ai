/**
 * WP-26 GIT-FLOW, the chat side: when "Work on a repository" and `/repo` are greyed and why, the refusal words, and
 * the plain timeline rows an engine run's repository work becomes.
 */
import { describe, expect, it, vi } from 'vitest';
import '@/lib/__tests__/test-i18n';
import i18n from 'i18next';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

import { builtinReason, findBuiltin, repoBuiltinReason, type BuiltinContext } from '../builtin-commands';
import { builtinReasonText } from '../components/chat-panel/slash-command-menu';
import { chatHasEngine, repositoryReason } from '../repository';
import { repositoryName } from '../repository-api';
import { gitPushArgs, pullRequestActivity, pushActivity, pushRefusal } from '../repository-steps';
import { withEngineSteps } from '../engine-steps';
import { admissionRefusalText, repositoryRefusalText } from '../harness-errors';
import type { MessagePart, SSEEngineProgressEvent } from '../types';
import type { CodingEngineView } from '@/app/(main)/workspace/ai-accounts/types';

const t = i18n.t.bind(i18n);

const engines = (overrides: Partial<CodingEngineView> = {}): CodingEngineView => ({
  engines: [
    { kind: 'codex', available: true, reason: null },
    { kind: 'claude_code', available: false, reason: 'no account' },
  ],
  orgDefault: 'codex',
  choice: 'org_default',
  withoutGvisor: false,
  gvisorSyscallFilter: null,
  autoRoute: true,
  ...overrides,
});

const base = { engines: engines(), engineChoice: 'choose' as const, agentChat: false, agentEngines: [], repository: null };

describe('why "Work on a repository" is greyed', () => {
  it('is usable when the chat resolves to an available engine', () => {
    expect(repositoryReason(base)).toBeNull();
    expect(repositoryReason({ ...base, chatEngine: 'codex' })).toBeNull();
    expect(repositoryReason({ ...base, heldEngine: 'codex' })).toBeNull();
  });

  it('needs an engine: the chat chose none, the person chose none, or the default is unavailable', () => {
    expect(repositoryReason({ ...base, chatEngine: 'none' })).toBe('noEngine');
    expect(repositoryReason({ ...base, heldEngine: 'none' })).toBe('noEngine');
    expect(repositoryReason({ ...base, engines: engines({ choice: 'none' }) })).toBe('noEngine');
    expect(repositoryReason({ ...base, engines: engines({ choice: 'claude_code' }) })).toBe('noEngine');
    expect(repositoryReason({ ...base, engineChoice: 'quickChat' })).toBe('noEngine');
  });

  it("reads the org's switch: no engine available at all", () => {
    const off = engines({ engines: [{ kind: 'codex', available: false, reason: 'off' }] });
    expect(repositoryReason({ ...base, engines: off, chatEngine: 'codex' })).toBe('orgOff');
  });

  it('a chat that already has a repository says so first', () => {
    expect(repositoryReason({ ...base, repository: 'acme/widgets', chatEngine: 'none' })).toBe('hasRepository');
  });

  it("an agent chat has an engine when the agent's profile lists one", () => {
    expect(chatHasEngine({ ...base, agentChat: true, agentEngines: ['codex'], chatEngine: 'none' })).toBe(true);
    expect(chatHasEngine({ ...base, agentChat: true, agentEngines: [] })).toBe(false);
  });

  it('is not known before the engine choices are read, so it is not greyed', () => {
    expect(repositoryReason({ ...base, engines: null })).toBeNull();
  });
});

const ALL: BuiltinContext = {
  running: false,
  conversation: true,
  answer: true,
  harness: true,
  forkable: true,
  reasoningModel: true,
  planStrategy: true,
  mcp: true,
  knowledge: true,
  memory: true,
  skills: true,
  workspace: true,
  agentChat: false,
  agentEditor: false,
  answers: 2,
};

describe('/repo in the slash menu', () => {
  const repo = findBuiltin('repo')!;

  it('is shipped and applies on the harness, before the first message too', () => {
    expect(repo).toBeDefined();
    expect(builtinReason(repo, ALL)).toBeNull();
    expect(builtinReason(repo, { ...ALL, conversation: false })).toBeNull();
    expect(builtinReason(repo, { ...ALL, harness: false })).toBe('legacy');
    expect(builtinReason(repo, { ...ALL, notOwner: true })).toBe('notOwner');
  });

  it('greys with each of the three reasons, in words', () => {
    expect(builtinReason(repo, { ...ALL, repo: 'noEngine' })).toBe('repoNoEngine');
    expect(builtinReason(repo, { ...ALL, repo: 'orgOff' })).toBe('repoOrgOff');
    expect(builtinReason(repo, { ...ALL, repo: 'hasRepository' })).toBe('repoTaken');
    expect(builtinReasonText(t, repo, 'repoNoEngine')).toBe('Needs a coding engine. Pick one first.');
    expect(builtinReasonText(t, repo, 'repoOrgOff')).toBe("Your organization hasn't turned on coding engines.");
    expect(builtinReasonText(t, repo, 'repoTaken', { repository: 'acme/widgets' })).toBe(
      'This chat already works on acme/widgets. Start a new chat for another repository.',
    );
    expect(repoBuiltinReason(null)).toBeNull();
  });

  it('has a description', () => {
    expect(t(`${repo.i18n}.description`)).toBe('Work on a GitHub repository in this chat');
  });
});

describe('refusals', () => {
  it('words each repository code, with or without the Node prefix', () => {
    expect(repositoryRefusalText('HTTP_AGENT_HARNESS_REPOSITORY_NEEDS_ENGINE')).toMatch(/needs a coding engine/);
    expect(repositoryRefusalText('AGENT_HARNESS_REPOSITORY_TOOLSET_MISSING')).toMatch(/Connect GitHub/);
    expect(repositoryRefusalText('HTTP_AGENT_HARNESS_REPOSITORY_NOT_GITHUB')).toMatch(/Only GitHub repositories/);
    expect(repositoryRefusalText('HTTP_AGENT_HARNESS_REPOSITORY_LOCKED')).toMatch(/Start a new chat/);
    expect(repositoryRefusalText('HTTP_500')).toBe("Couldn't save the repository. Try again.");
  });

  it("a new chat's first message refused for its repository reads the same", () => {
    expect(admissionRefusalText('HTTP_AGENT_HARNESS_REPOSITORY_LOCKED')).toBe(repositoryRefusalText('REPOSITORY_LOCKED'));
    expect(admissionRefusalText('AGENT_HARNESS_REPOSITORY_NEEDS_ENGINE')).toBe(repositoryRefusalText('REPOSITORY_NEEDS_ENGINE'));
  });
});

describe('repositoryName', () => {
  it('is owner/name of a GitHub URL', () => {
    expect(repositoryName('https://github.com/acme/widgets')).toBe('acme/widgets');
    expect(repositoryName('https://github.com/acme/widgets.git')).toBe('acme/widgets');
    expect(repositoryName('https://example.com/x')).toBe('https://example.com/x');
  });
});

const SESSION = 'pipeshub/session-4f2c';

describe('a git push as a timeline row', () => {
  it('finds the push in a command line', () => {
    expect(gitPushArgs('git push origin HEAD')).toEqual(['origin', 'HEAD']);
    expect(gitPushArgs('cd repo && git push -u origin pipeshub/session-4f2c')).toEqual(['-u', 'origin', SESSION]);
    expect(gitPushArgs('git status')).toBeNull();
  });

  it('pushed to the session branch', () => {
    expect(pushActivity('git push origin HEAD', false, SESSION)).toEqual({ action: 'pushed', branch: SESSION });
    expect(pushActivity('git push origin feature', false, null)).toEqual({ action: 'pushed', branch: 'feature' });
  });

  it('says why the proxy refused it', () => {
    expect(pushRefusal(['origin', 'main'], SESSION)).toBe('otherBranch');
    expect(pushRefusal(['origin', `HEAD:refs/heads/main`], SESSION)).toBe('otherBranch');
    expect(pushRefusal(['--force', 'origin', SESSION], SESSION)).toBe('overwrite');
    expect(pushRefusal(['-f', 'origin', SESSION], SESSION)).toBe('overwrite');
    expect(pushRefusal(['origin', `+${SESSION}`], SESSION)).toBe('overwrite');
    expect(pushRefusal(['--delete', 'origin', SESSION], SESSION)).toBe('delete');
    expect(pushRefusal(['origin', `:${SESSION}`], SESSION)).toBe('delete');
    expect(pushRefusal(['--tags', 'origin'], SESSION)).toBe('tags');
    expect(pushRefusal(['origin', SESSION], SESSION)).toBe('other');
    expect(pushActivity('git push origin main', true, SESSION)).toEqual({ action: 'pushRefused', target: 'main', reason: 'otherBranch' });
  });
});

describe('an opened pull request as a timeline row', () => {
  const pr = '{"url":"https://api.github.com/repos/acme/widgets/pulls/7","id":1,"html_url":"https://github.com/acme/widgets/pull/7","number":7,"state":"open","user":{"html_url":"https://github.com/octo"';

  it('reads the number and link from GitHub JSON cut short', () => {
    expect(pullRequestActivity(pr, false, null)).toEqual({
      action: 'prOpened',
      repository: 'acme/widgets',
      number: 7,
      url: 'https://github.com/acme/widgets/pull/7',
    });
  });

  it('has no link without a github.com pull URL, and a failure is one plain row', () => {
    expect(pullRequestActivity('{"number": 12', false, 'acme/widgets')).toEqual({
      action: 'prOpened',
      repository: 'acme/widgets',
      number: 12,
      url: null,
    });
    expect(pullRequestActivity(pr, true, null)).toEqual({ action: 'prFailed' });
    expect(pullRequestActivity('{"success": false, "error": "Validation Failed"}', false, null)).toEqual({ action: 'prFailed' });
  });
});

describe('withEngineSteps', () => {
  const frame = (kind: string, text: string, data: Record<string, unknown> = {}): SSEEngineProgressEvent => ({
    tool: 'engine',
    taskId: 't1',
    kind,
    text,
    data,
  });

  it('turns the copy, the pushes and the pull request into plain rows; other steps stay', () => {
    const frames = [
      frame('repository', 'acme/widgets', { action: 'copied', branch: SESSION }),
      frame('command', 'npm test', { callId: 'c1' }),
      frame('command_done', '', { callId: 'c1', exitCode: 0 }),
      frame('command', 'git push origin HEAD', { callId: 'c2' }),
      frame('command_done', '', { callId: 'c2', exitCode: 0 }),
      frame('command', 'git push --force origin main', { callId: 'c3' }),
      frame('output', 'remote: refused by proxy rule 4 (ref refs/heads/main)\n', { callId: 'c3' }),
      frame('command_done', '', { callId: 'c3', exitCode: 1 }),
      frame('tool', 'github.open_pull_request', { callId: 't1' }),
      frame('tool_result', '', { callId: 't1', status: 'completed', result: '{"html_url":"https://github.com/acme/widgets/pull/7","number":7' }),
    ];
    const parts: MessagePart[] = [{ type: 'engine_task', runId: 'r1', frames }];
    const rows = withEngineSteps(parts, true);
    expect(rows.map((r) => r.type)).toEqual(['engine_repo', 'engine_command', 'engine_repo', 'engine_repo', 'engine_repo']);
    expect(rows[0].repo).toEqual({ action: 'copied', repository: 'acme/widgets', branch: SESSION });
    expect(rows[2].repo).toEqual({ action: 'pushed', branch: SESSION });
    expect(rows[3]).toMatchObject({ status: 'failed', repo: { action: 'pushRefused', target: 'main', reason: 'otherBranch' } });
    // A refused push never carries its raw output into the row.
    expect(rows[3].content).toBeUndefined();
    expect(rows[4].repo).toMatchObject({ action: 'prOpened', number: 7 });
  });

  it('a push still running is an ordinary command row until it ends', () => {
    const parts: MessagePart[] = [{ type: 'engine_task', frames: [frame('command', 'git push origin HEAD', { callId: 'c1' })] }];
    expect(withEngineSteps(parts, false)[0].type).toBe('engine_command');
  });
});
