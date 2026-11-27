import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { inlineContentRef } from '../../../../src/modules/agent-harness/domain/run-content';

export interface ModelCall {
  callId: string;
  name: string;
  args: Record<string, unknown>;
}

/**
 * A run's journal as its worker and Node write it, event by event, for the
 * activity panel's tests (UX5): envelopes, sequence numbers and timestamps
 * filled in, tool calls invoked as `t<turn>.<index>`.
 */
export class ActivityJournal {
  readonly events: JournalEvent[] = [];

  constructor(
    readonly runId = 'run-ux5',
    readonly sessionId = 'ses-ux5',
    readonly orgId = 'org-ux5',
  ) {}

  get seq(): number {
    return this.events.length;
  }

  add(type: string, data: Record<string, unknown>, writer: 'worker' | 'node' = 'worker'): JournalEvent {
    const seq = this.events.length + 1;
    const event = {
      orgId: this.orgId,
      sessionId: this.sessionId,
      streamId: this.sessionId,
      seq,
      runId: this.runId,
      attemptId: `${this.runId}.a1`,
      fence: 1,
      type,
      schemaVersion: 1,
      critical: !type.startsWith('x.'),
      ts: new Date(Date.UTC(2026, 9, 9, 12, 0, seq)).toISOString(),
      writer,
      visibility: 'owner',
      data,
    } as JournalEvent;
    this.events.push(event);
    return event;
  }

  turn(turn: number, calls: ModelCall[], text = ''): this {
    this.add('turn.started', { turn });
    const message = Buffer.from(
      JSON.stringify({ text, toolCalls: calls.map(({ callId, name, args }) => ({ id: callId, name, args })) }),
    );
    this.add('model.response', {
      messageRef: inlineContentRef(message),
      toolCalls: calls.map(({ callId, name }) => ({ callId, modelName: name, argsDigest: `sha256:${'a'.repeat(64)}` })),
    });
    return this;
  }

  intent(invocationId: string, toolId: string): this {
    this.add('tool.intent', {
      invocationId,
      toolId,
      argsDigest: `sha256:${'b'.repeat(64)}`,
      effect: 'write',
      sink: { egress: 'none', argsLeaveBoundary: false },
    });
    return this;
  }

  outcome(invocationId: string, status = 'ok'): this {
    this.add('tool.outcome', { invocationId, status });
    return this;
  }

  denied(invocationId: string, rule: string): this {
    this.add('tool.denied', { invocationId, rule });
    return this;
  }

  /** The worker's record of a finished command (`x.pipeshub.command.finished`). */
  commandFinished(data: Partial<Record<string, unknown>> & { commandId: string }): this {
    this.add('x.pipeshub.command.finished', {
      source: 'run_code',
      command: '',
      status: 'completed',
      exitCode: 0,
      startedAt: 1_759_320_000_000,
      durationMs: 412,
      stdout: { tail: '', bytes: 0, truncated: false },
      stderr: { tail: '', bytes: 0, truncated: false },
      liveDropped: 0,
      ...data,
    });
    return this;
  }

  /** A call held for a person (UX1): why, then the ask. */
  held(interactionId: string, reason: Record<string, unknown>): this {
    this.add('interaction.explained', { interactionId, ...reason });
    this.add('interaction.requested', { interactionId, kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` });
    return this;
  }

  decided(interactionId: string, decision: 'approved' | 'denied'): this {
    this.add('interaction.resolved', { interactionId, decision, by: 'p_owner', channel: 'web' }, 'node');
    return this;
  }

  engine(kind: string, text: string, data: Record<string, unknown> = {}): this {
    this.add('x.pipeshub.engine.progress', {
      tool: 'code_task',
      taskId: this.runId,
      kind,
      text,
      data,
      turnId: 'trn_1',
      seq: this.seq + 1,
    });
    return this;
  }

  /** `kinds` absent: an event written before R27REST-FU-1. */
  committed(changed: string[], revision = 1, kinds?: Record<string, string>): this {
    this.add('workspace.committed', { workspaceId: 'ws-1', revision, changed, ...(kinds !== undefined ? { kinds } : {}) });
    return this;
  }

  task(type: 'task.created' | 'task.updated', taskId: string, title: string, status: string): this {
    this.add(type, { taskId, title, status, deps: [] });
    return this;
  }

  completed(): this {
    this.add('run.completed', { outcome: 'answered' }, 'node');
    return this;
  }

  failed(errorType = 'worker_failed'): this {
    this.add('run.failed', { errorType }, 'node');
    return this;
  }
}

export const RUN_CODE_TOOL = 'pipeshub@coding_sandbox/run_code';
export const SLACK_TOOL = 'slack@default/send_message';

export const FLOW_REASON = {
  rule: 'flow_external_write',
  toolId: SLACK_TOOL,
  sensitivity: 'private',
  sources: [{ title: 'Q3 plan', recordId: 'rec-1' }],
  moreSources: 0,
  destination: { label: '#sales', key: `sha256:${'2'.repeat(64)}` },
  conversationGrant: false,
};

/**
 * AP-1's run: `run_code` that ran and printed, a Slack message held for
 * approval then granted, a call policy refused, one the person denied, and an
 * engine that planned, ran two commands (one still running), changed and
 * diffed a file, and saved a revision.
 */
export function activityScenario(journal = new ActivityJournal()): ActivityJournal {
  return journal
    .turn(1, [
      { callId: 'c-run', name: 'run_code', args: { code: "print('hi')", language: 'python' } },
      { callId: 'c-send', name: 'slack__send_message', args: { channel: '#sales', text: 'Q3' } },
      { callId: 'c-del', name: 'drive__delete_file', args: { id: 'f-1' } },
      { callId: 'c-post', name: 'web__post', args: { url: 'https://example.com' } },
    ])
    .intent('t1.0', RUN_CODE_TOOL)
    .commandFinished({
      commandId: 't1.0',
      invocationId: 't1.0',
      command: "print('hi')",
      language: 'python',
      stdout: { tail: 'hi\n', bytes: 3, truncated: false },
      log: { ref: 'inline:aGkK', hash: `sha256:${'c'.repeat(64)}`, size: 3 },
    })
    .outcome('t1.0')
    .held('ix.approve.t1.1', FLOW_REASON)
    .decided('ix.approve.t1.1', 'approved')
    .intent('t1.1', SLACK_TOOL)
    .outcome('t1.1')
    .denied('t1.2', 'policy')
    .held('ix.approve.t1.3', { ...FLOW_REASON, toolId: 'web@default/post' })
    .decided('ix.approve.t1.3', 'denied')
    .denied('t1.3', 'interaction_denied')
    .engine('started', 'Fix the failing test')
    .task('task.created', 'task-1', 'Read the failing test', 'in_progress')
    .task('task.created', 'task-2', 'Fix the parser', 'pending')
    .engine('command', 'pytest -q', { callId: 'e1' })
    .engine('output', '1 failed\n', { callId: 'e1', stream: 'stdout', truncated: false })
    .commandFinished({
      commandId: 'e1',
      source: 'engine',
      taskId: 'run-ux5',
      command: 'pytest -q',
      status: 'failed',
      exitCode: 1,
      stdout: { tail: '1 failed\n', bytes: 9, truncated: false },
    })
    .engine('command_done', '', { callId: 'e1', exitCode: 1, status: 'failed' })
    .task('task.updated', 'task-1', 'Read the failing test', 'completed')
    .engine('file', 'src/parser.py', { change: 'modified' })
    .engine('diff', 'src/parser.py', { change: 'modified', diff: '-a\n+b', truncated: false, sizeBytes: 2, mimeType: 'text/x-python' })
    .committed(['/workspace/report.md'])
    .engine('command', 'pytest -q tests/test_parser.py', { callId: 'e2' })
    .engine('output', 'collecting', { callId: 'e2', stream: 'stdout', truncated: false });
}
