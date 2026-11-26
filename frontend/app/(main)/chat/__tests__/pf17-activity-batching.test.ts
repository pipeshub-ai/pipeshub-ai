/**
 * PF-17 AB-1..AB-3: a burst of live output (10 MB of a build's log in 4 KiB events) is folded once, not once per
 * event: waiting output to the same command and stream is joined, and folding the batch gives what folding each
 * event did. The browser lane (tests/e2e/perf/chat-large-run.perf.spec.ts) measures the input latency this keeps.
 */
import { describe, expect, it } from 'vitest';
import { ACTIVITY_TERMINAL_BOUNDS, enqueueActivityEvent, reduceActivity } from '../activity';
import type { ActivityCommand, ActivityEvent, ActivityView } from '../types';

const command = (commandId: string): ActivityCommand => ({
  commandId,
  source: 'engine',
  command: `make ${commandId}`,
  status: 'running',
  exitCode: null,
  startedAt: 1,
  durationMs: null,
  stdout: '',
  stderr: '',
  truncated: false,
  liveDropped: 0,
  hasLog: false,
});

const view = (): ActivityView =>
  ({
    runId: 'run-1',
    status: 'running',
    engine: true,
    commands: [command('a'), command('b')],
    omittedCommands: 0,
    tools: [],
    tasks: [],
    files: [],
    followups: [],
  }) as ActivityView;

const output = (commandId: string, text: string, stream: 'stdout' | 'stderr' = 'stdout'): ActivityEvent => ({
  type: 'output',
  commandId,
  stream,
  text,
});

function batched(events: ActivityEvent[]): ActivityEvent[] {
  const pending: ActivityEvent[] = [];
  for (const event of events) enqueueActivityEvent(pending, event);
  return pending;
}

describe('PF-17 folding activity events in batches', () => {
  it('AB-1: output to the same command and stream is joined; anything else stays its own event, in order', () => {
    const events: ActivityEvent[] = [
      output('a', 'one\n'),
      output('a', 'two\n'),
      output('a', 'oops\n', 'stderr'),
      output('b', 'b1\n'),
      { type: 'output', commandId: 'b', stream: 'stdout', dropped: 10 },
      output('b', 'b2\n'),
      { type: 'command', command: { ...command('a'), status: 'completed', exitCode: 0 } },
      output('a', 'three\n'),
    ];
    const pending = batched(events);
    expect(pending.map((e) => (e.type === 'output' ? `${e.commandId}:${e.stream}:${e.text ?? `-${e.dropped}`}` : e.type))).toEqual([
      'a:stdout:one\ntwo\n',
      'a:stderr:oops\n',
      'b:stdout:b1\n',
      'b:stdout:-10',
      'b:stdout:b2\n',
      'command',
      'a:stdout:three\n',
    ]);
    expect(pending.reduce(reduceActivity, view())).toEqual(events.reduce(reduceActivity, view()));
  });

  it('AB-2: 10 MB in 4 KiB events is one event holding a bounded tail, and folds the same', () => {
    const line = `${'.'.repeat(78)}\n`;
    const chunk = line.repeat(52);
    const events: ActivityEvent[] = [];
    for (let sent = 0, n = 0; sent < 10 * 1024 * 1024; sent += chunk.length, n += 1) events.push(output('a', `${n}:${chunk}`));
    const pending = batched(events);
    expect(pending).toHaveLength(1);
    const [only] = pending;
    expect(only.type === 'output' && only.text!.length).toBeLessThanOrEqual(4 * ACTIVITY_TERMINAL_BOUNDS.chars);
    expect(only.type === 'output' && only.truncated).toBe(true);
    const once = pending.reduce(reduceActivity, view())!;
    const each = events.reduce(reduceActivity, view())!;
    expect(once.commands[0]).toEqual(each.commands[0]);
    expect(once.commands[0].stdout.length).toBeLessThanOrEqual(ACTIVITY_TERMINAL_BOUNDS.chars);
    expect(once.commands[0].truncated).toBe(true);
  });

  it('AB-3: an escape sequence split across two events is stripped once they are joined', () => {
    const folded = batched([output('a', 'red \u001b[3'), output('a', '1mtext\u001b[0m\n')]).reduce(reduceActivity, view())!;
    expect(folded.commands[0].stdout).toBe('red text\n');
  });
});
