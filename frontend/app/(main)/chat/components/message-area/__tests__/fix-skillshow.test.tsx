/**
 * FIX-SKILLSHOW: a step that read a skill is its own plain, closed step ("Read the PDF skill") on the answer's
 * timeline and in the activity panel, for any engine: the server names the skills (`skills`), the chat words them,
 * and the command the step ran stays one click away.
 */
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { EngineCommandRow, toolActivityLabel } from '../agent-activity';
import { CommandBlock } from '../../activity-panel/command-block';
import { partOfTool, toolOfPart } from '../../activity-panel/activity-steps';
import { engineSteps, withEngineSteps } from '../../../engine-steps';
import { reduceActivity, toActivityEvent } from '../../../activity';
import { skillDisplayName, skillNames, skillReadLabel } from '../../../utils/tool-display';
import type { ActivityCommand, ActivityView, MessagePart, SSEEngineProgressEvent } from '../../../types';

afterEach(() => cleanup());

const CODEX_CHAIN =
  `/bin/bash -lc "cat /pipeshub/AGENTS.md && printf '\\n---PDF SKILL---\\n' && cat /skills/pdf/SKILL.md && ` +
  `printf '\\n---TOOLS INDEX---\\n' && cat /pipeshub/tools/INDEX.md"`;

const frame = (kind: string, text: string, data: Record<string, unknown>): SSEEngineProgressEvent =>
  ({ tool: 'code_task', taskId: 'run_1', kind, text, data }) as SSEEngineProgressEvent;

const FRAMES: SSEEngineProgressEvent[] = [
  frame('command', CODEX_CHAIN, { callId: 'c1', skills: ['pdf'] }),
  frame('output', '# AGENTS\n', { callId: 'c1' }),
  frame('command_done', '', { callId: 'c1', exitCode: 0 }),
  frame('tool', 'Skill', { callId: 'c2', args: '{"skill":"pdf"}', skills: ['pdf'] }),
  frame('tool_result', 'Skill', { callId: 'c2', status: 'completed' }),
  frame('command', 'node make-pdf.js', { callId: 'c3' }),
];

const command = (overrides: Partial<ActivityCommand> = {}): ActivityCommand => ({
  commandId: 'c1',
  source: 'engine',
  command: CODEX_CHAIN,
  status: 'completed',
  exitCode: 0,
  startedAt: 1,
  durationMs: 40,
  stdout: '# AGENTS\n',
  stderr: '',
  truncated: false,
  liveDropped: 0,
  hasLog: false,
  ...overrides,
});

describe('FIX-SKILLSHOW the words', () => {
  it('names a skill by its folder name in words, acronyms kept', () => {
    expect(skillDisplayName('pdf')).toBe('PDF');
    expect(skillDisplayName('brand-guidelines')).toBe('Brand Guidelines');
    expect(skillDisplayName('plugin:pdf')).toBe('PDF');
    expect(skillReadLabel(['pdf'])).toBe('Read the PDF skill');
    expect(skillReadLabel(['pdf'], true)).toBe('Reading the PDF skill');
    expect(skillReadLabel(['pdf', 'brand-guidelines'])).toBe('Read the PDF and Brand Guidelines skills');
    expect(skillReadLabel(['pdf', 'csv'], true)).toBe('Reading the PDF and CSV skills');
  });

  it('takes only skill names from the server', () => {
    expect(skillNames(['pdf', 7, '../x', ''])).toEqual(['pdf']);
    expect(skillNames([])).toBeUndefined();
    expect(skillNames('pdf')).toBeUndefined();
  });
});

describe('FIX-SKILLSHOW the timeline', () => {
  it('a command or tool step keeps the skills its frame named; a plain command has none', () => {
    const parts = withEngineSteps([{ type: 'engine_task', runId: 'run_1', frames: FRAMES } as MessagePart], true);
    expect(parts.map((part) => [part.type, part.skills])).toEqual([
      ['engine_command', ['pdf']],
      ['tool_call', ['pdf']],
      ['engine_command', undefined],
    ]);
    expect(engineSteps(FRAMES, true)[0]).toMatchObject({ kind: 'command', command: CODEX_CHAIN, skills: ['pdf'] });
    expect(toolActivityLabel(parts[1]!)).toBe('Read the PDF skill');
  });

  it('the chained command reads "Read the PDF skill", closed; opened, it shows the command and its output', () => {
    const [part] = withEngineSteps([{ type: 'engine_task', runId: 'run_1', frames: FRAMES } as MessagePart], true);
    render(
      <Theme>
        <EngineCommandRow part={part!} />
      </Theme>,
    );
    expect(screen.getByTestId('engine-command-skill').textContent).toBe('Read the PDF skill');
    expect(screen.queryByTestId('engine-command-line')).toBeNull();
    expect(screen.queryByText(/AGENTS\.md/)).toBeNull();
    const row = screen.getByRole('button');
    expect(row.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(row);
    expect(screen.getByTestId('engine-command-line').textContent).toBe(CODEX_CHAIN);
    expect(screen.getByTestId('engine-command-output').textContent).toContain('# AGENTS');
  });

  it('a plain command still reads "Ran <command>"', () => {
    render(
      <Theme>
        <EngineCommandRow part={{ type: 'engine_command', toolCallId: 'c3', command: 'node make-pdf.js', status: 'completed' }} />
      </Theme>,
    );
    expect(screen.queryByTestId('engine-command-skill')).toBeNull();
    expect(screen.getByText('node make-pdf.js')).toBeTruthy();
  });
});

describe('FIX-SKILLSHOW the activity panel', () => {
  it("a command's skills come through the snapshot and live upserts; anything else is dropped", () => {
    const event = toActivityEvent('command', { ...command(), skills: ['pdf', 3] });
    expect(event).toMatchObject({ type: 'command', command: { skills: ['pdf'] } });
    expect(toActivityEvent('command', command())).not.toHaveProperty('command.skills');
    const view: ActivityView = {
      runId: 'r',
      status: 'running',
      engine: true,
      commands: [command({ skills: ['pdf'], status: 'running' })],
      omittedCommands: 0,
      tools: [],
      tasks: [],
      files: [],
      followups: [],
    };
    const next = reduceActivity(view, { type: 'command', command: command({ status: 'completed' }) });
    expect(next?.commands[0]?.skills, 'an upsert without them keeps what the panel knew').toEqual(['pdf']);
  });

  it('a skill command is a closed step naming the skill; its terminal opens on a click', () => {
    render(
      <Theme>
        <CommandBlock command={command({ skills: ['pdf'] })} onOpenLog={() => {}} />
      </Theme>,
    );
    const toggle = screen.getByRole('button', { name: 'Read the PDF skill' });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByTestId('activity-command')).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByTestId('activity-command').textContent).toContain('cat /skills/pdf/SKILL.md');
  });

  it('a plain command is the terminal block it always was', () => {
    render(
      <Theme>
        <CommandBlock command={command({ command: 'node make-pdf.js' })} onOpenLog={() => {}} />
      </Theme>,
    );
    expect(screen.queryByTestId('activity-skill-command')).toBeNull();
    expect(screen.getByTestId('activity-command').textContent).toContain('node make-pdf.js');
  });

  it("an engine's Skill call keeps its skills as an activity step", () => {
    const part: MessagePart = { type: 'tool_call', toolCallId: 'c2', toolName: 'Skill', status: 'completed', skills: ['pdf'] };
    const tool = toolOfPart(part)!;
    expect(tool.skills).toEqual(['pdf']);
    expect(partOfTool(tool).skills).toEqual(['pdf']);
  });
});
