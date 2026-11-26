/**
 * SLASH1 authoring: the skills editor's Command tab turns a skill into a /command, declares its arguments, previews
 * the text with sample arguments, and "Try it" opens a new chat with the composer holding the command.
 */
import React, { useEffect, useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const router = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => router }));

import { useComposerPrefill } from '@/chat/commands';
import { ShadowedCommandsNotice, SkillCommandTab, commandProblem, shadowedCommands } from '../components/skill-command-tab';
import type { CommandShare, CommandShareTargets, Skill } from '../types';

const BASE: Skill = {
  name: 'triage',
  description: 'Triage',
  version: '1.0.3',
  category: null,
  subcategory: null,
  tags: [],
  status: 'active',
  source: 'manual',
  license: null,
  compatibility: null,
  allowedTools: null,
  related: [],
  requires: [],
  concepts: [],
  deprecatedReason: null,
  replacedBy: null,
  createdAt: null,
  updatedAt: null,
  packName: null,
  packVersion: null,
  invocation: { user: false, model: true },
  arguments: [],
  body: 'Look up $incident.',
  resources: {},
};

let latest: Skill = BASE;

function Harness({ initial, name = 'triage', saved = false, isCreate = true }: { initial: Skill; name?: string; saved?: boolean; isCreate?: boolean }) {
  const [skill, setSkill] = useState(initial);
  useEffect(() => {
    latest = skill;
  });
  return (
    <Theme>
      <SkillCommandTab skill={skill} name={name} setSkill={setSkill} readOnly={false} isCreate={isCreate} saved={saved} onTry={() => undefined} />
    </Theme>
  );
}

beforeEach(() => {
  router.push.mockReset();
  useComposerPrefill.setState({ prefill: null });
});
afterEach(() => cleanup());

describe('the skills editor Command tab', () => {
  it('"Usable as a /command" makes a new skill a command the agent does not load on its own', () => {
    render(<Harness initial={BASE} />);
    fireEvent.click(screen.getByRole('switch', { name: 'Usable as a /command' }));
    expect(latest.invocation).toEqual({ user: true, model: false });
    expect(screen.getByRole('switch', { name: 'The agent may also use it on its own' }).getAttribute('aria-checked')).toBe('false');
  });

  it('declares arguments and previews the text with sample arguments', () => {
    render(<Harness initial={{ ...BASE, invocation: { user: true, model: false } }} />);
    fireEvent.click(screen.getByRole('button', { name: /Add argument/ }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'incident' } });
    fireEvent.click(screen.getByRole('switch', { name: 'Required' }));
    expect(latest.arguments).toEqual([{ name: 'incident', required: true }]);

    expect(screen.getByTestId('skill-command-preview').textContent).toBe('Missing: incident');
    fireEvent.change(screen.getByRole('textbox', { name: 'Sample arguments' }), { target: { value: 'INC-7' } });
    expect(screen.getByTestId('skill-command-preview').textContent).toBe('Look up INC-7.');
  });

  it('refuses an engine word as the name, as the server does', () => {
    const skill = { ...BASE, invocation: { user: true, model: false } };
    expect(commandProblem(skill, 'compact')).toBe('reserved');
    expect(commandProblem({ ...skill, arguments: [{ name: 'Bad' }] }, 'triage')).toBe('argumentName');
    expect(commandProblem({ ...skill, arguments: [{ name: 'a' }, { name: 'a' }] }, 'triage')).toBe('duplicate');
    expect(commandProblem({ ...skill, invocation: { user: false, model: true } }, 'compact')).toBeNull();
    render(<Harness initial={skill} name="compact" />);
    expect(screen.getByRole('alert').textContent).toBe('/compact is reserved. Choose another name.');
  });

  it('"Try it" waits for a save, then opens a new chat holding the command', () => {
    const skill = { ...BASE, invocation: { user: true, model: false } };
    const { unmount } = render(<Harness initial={skill} />);
    expect((screen.getByTestId('skill-command-try') as HTMLButtonElement).disabled).toBe(true);
    unmount();

    render(<Harness initial={skill} saved isCreate={false} />);
    fireEvent.click(screen.getByTestId('skill-command-try'));
    expect(router.push).toHaveBeenCalledWith('/chat');
    expect(useComposerPrefill.getState().prefill?.text).toBe('/triage ');
  });
});

describe('"Who can use it" (SLASH1 S4)', () => {
  const COMMAND = { ...BASE, invocation: { user: true, model: false } };
  const TARGETS: CommandShareTargets = { org: true, teams: [{ id: 'ops', name: 'Ops' }] };

  function ShareHarness({ skill, targets, initial }: { skill: Skill; targets: CommandShareTargets | null; initial: CommandShare }) {
    const [share, setShare] = useState(initial);
    useEffect(() => {
      latestShare = share;
    });
    return (
      <Theme>
        <SkillCommandTab
          skill={skill}
          name="triage"
          setSkill={() => undefined}
          readOnly={false}
          isCreate={false}
          saved
          onTry={() => undefined}
          share={share}
          onShareChange={setShare}
          shareTargets={targets}
        />
      </Theme>
    );
  }
  let latestShare: CommandShare = { scope: 'personal', teamId: null };

  it('offers only you, the teams the author owns and, for an org admin, everyone', () => {
    render(<ShareHarness skill={COMMAND} targets={TARGETS} initial={{ scope: 'personal', teamId: null }} />);
    const options = screen.getAllByRole('radio').map((r) => r.closest('label')?.textContent);
    expect(options).toEqual(['Only you', 'Team: Ops', 'Everyone in your organization']);
    fireEvent.click(screen.getByRole('radio', { name: 'Team: Ops' }));
    expect(latestShare).toEqual({ scope: 'team', teamId: 'ops' });
    fireEvent.click(screen.getByRole('radio', { name: 'Everyone in your organization' }));
    expect(latestShare).toEqual({ scope: 'org', teamId: null });
  });

  it('is not shown when there is nowhere to share to', () => {
    render(<ShareHarness skill={COMMAND} targets={{ org: false, teams: [] }} initial={{ scope: 'personal', teamId: null }} />);
    expect(screen.queryByTestId('skill-command-share')).toBeNull();
  });

  it('keeps the team it is shared with even when the editor does not own it (an org admin\'s edit)', () => {
    render(
      <ShareHarness
        skill={{ ...COMMAND, scope: 'team', teamId: 'sales', team: 'Sales' }}
        targets={{ org: true, teams: [] }}
        initial={{ scope: 'team', teamId: 'sales' }}
      />,
    );
    expect(screen.getByRole('radio', { name: 'Team: Sales' }).getAttribute('aria-checked')).toBe('true');
  });
});

describe('SLASH2 O2: a command a built-in now hides', () => {
  afterEach(() => window.localStorage.clear());

  it('a new one may not take a built-in\'s name; a saved one is marked hidden, never renamed', () => {
    const skill = { ...BASE, invocation: { user: true, model: false } };
    expect(commandProblem(skill, 'status')).toBe('reserved');
    expect(commandProblem(skill, 'status', true)).toBe('shadowed');
    render(<Harness initial={skill} name="status" isCreate={false} saved />);
    expect(screen.getByTestId('skill-command-problem').textContent).toBe('Hidden by the built-in /status. Rename it to use it.');
  });

  it('lists the hidden commands once, until dismissed; a new collision shows it again', () => {
    const skills = [
      { name: 'status', invocation: { user: true, model: false } },
      { name: 'usage', invocation: { user: true, model: false } },
      { name: 'stop', invocation: { user: false, model: true } },
      { name: 'triage', invocation: { user: true, model: false } },
    ];
    expect(shadowedCommands(skills)).toEqual(['status', 'usage']);
    const { unmount } = render(<Theme><ShadowedCommandsNotice skills={skills} /></Theme>);
    expect(screen.getByTestId('shadowed-commands-notice').textContent).toContain('/status  /usage');
    fireEvent.click(screen.getByRole('button', { name: 'Got it' }));
    expect(screen.queryByTestId('shadowed-commands-notice')).toBeNull();
    unmount();
    render(<Theme><ShadowedCommandsNotice skills={skills} /></Theme>);
    expect(screen.queryByTestId('shadowed-commands-notice')).toBeNull();
    cleanup();
    render(<Theme><ShadowedCommandsNotice skills={[...skills, { name: 'fork', invocation: { user: true, model: false } }]} /></Theme>);
    expect(screen.getByTestId('shadowed-commands-notice')).toBeTruthy();
  });
});
