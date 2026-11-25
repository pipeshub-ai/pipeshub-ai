import { expect } from 'chai';
import {
  HarnessOperatorControlRefusedError,
  type OperatorControlCode,
} from '../../../../src/modules/agent-harness/domain/errors';
import { readFileSync } from 'fs';
import { join } from 'path';
import {
  ERROR_CODES,
  WIRE_PREFIX,
  type ErrorCode,
  type ErrorCodeOf,
} from '../../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/errors';
import {
  ActivityPanelDisabledError,
  ConversationDeletedError,
  HarnessEngineProfileRefusedError,
  RunNotCompletedError,
  HarnessApprovalsRefusedError,
  HarnessConflictError,
  HarnessDecisionRefusedError,
  HarnessModelAccessRefusedError,
  HarnessModelAccountRefusedError,
  HarnessQuotaExceededError,
  HarnessRunConfigUnavailableError,
  HarnessScheduleRefusedError,
  HarnessSessionRoleRefusedError,
  HarnessGroupAccessRefusedError,
  type GroupAccessCode,
  HarnessTriggerRefusedError,
  HarnessMemoryRefusedError,
  HarnessUnauthorizedError,
  JournalPayloadMissingError,
  JournalRejectedError,
  SessionErasedError,
  type HarnessConflictCode,
  type HarnessUnauthorizedReason,
  type JournalRejectionCode,
  type ApprovalsRefusalCode,
  type MemoryRefusalCode,
  type ScheduleRefusalCode,
} from '../../../../src/modules/agent-harness/domain/errors';
import type { EngineProfileRefusalCode } from '../../../../src/modules/agent-harness/domain/engine-profiles';
import type { AccessRefusalCode } from '../../../../src/modules/agent-harness/domain/model-access';
import type { ModelAccountRefusalCode } from '../../../../src/modules/agent-harness/domain/model-accounts';
import type { TurnRefusal } from '../../../../src/modules/agent-harness/domain/session-roles';
import { PublicationRefusedError } from '../../../../src/modules/agent-harness/application/knowledge-publications';
import { publicationHttpError } from '../../../../src/modules/agent-harness/transport/publication-routes';
import { scheduleHttpError } from '../../../../src/modules/agent-harness/transport/schedule-routes';
import type { DecisionRefusal } from '../../../../src/modules/agent-harness/domain/interactions';
import {
  WorkspaceError,
  type WorkspaceErrorCode,
} from '../../../../src/modules/agent-harness/domain/workspace';
import { transportError as publicTransportError } from '../../../../src/modules/agent-harness/transport/errors';
import { workspaceRefusal } from '../../../../src/modules/agent-harness/transport/workspace-controller';
import { harnessUnauthorized } from '../../../../src/modules/agent-harness/transport/internal-auth';
import { transportError } from '../../../../src/modules/agent-harness/transport/internal-controller';
import { CONTRACTS_DIR, overlayErrorCodes } from '../../../support/harness/contracts';

/**
 * CPD0-23, QA7-7: the codes Node answers its internal routes with, and the
 * public refusals the frontend and the worker key on, are exactly
 * `contracts/harness/ext/pipeshub/errors.json`, at the catalogue's status.
 * The Python journal client is held to the same file by
 * backend/python/tests/unit/harness/test_journal_wire_codes.py.
 */
const catalog = JSON.parse(
  readFileSync(join(CONTRACTS_DIR, 'ext', 'pipeshub', 'errors.json'), 'utf8'),
) as { wirePrefix: string; codes: Array<{ code: string; status: number; group?: string }> };

/** What the build generates: this repository's codes, then those an edition's overlay catalog adds. */
const generated = [...catalog.codes, ...overlayErrorCodes<(typeof catalog.codes)[number]>(join('ext', 'pipeshub'))];

const codesOf = (...groups: string[]): string[] =>
  catalog.codes.filter((c) => groups.includes(c.group ?? '')).map((c) => c.code);

const UNAUTHORIZED: readonly HarnessUnauthorizedReason[] = [
  'credential', 'malformed', 'algorithm', 'unknown_key', 'signature',
  'issuer', 'audience', 'expired', 'claims', 'superseded',
];

/** What the worker reads: Node's code without its `HTTP_` and wire prefixes, and the status. */
function answered(error: unknown): { code: string; status: number } {
  const http = error as { code: string; statusCode: number };
  return {
    code: http.code.replace(/^HTTP_/, '').replace(new RegExp(`^${WIRE_PREFIX}`), ''),
    status: http.statusCode,
  };
}

function sent(): Map<string, number> {
  const out = new Map<string, number>();
  const add = (error: unknown): void => {
    const { code, status } = answered(error);
    out.set(code, status);
  };
  for (const code of codesOf('conflict', 'budget')) {
    add(transportError(new HarnessConflictError(code as HarnessConflictCode, 'm')));
  }
  for (const code of codesOf('journal_rejected')) {
    add(transportError(new JournalRejectedError(code.replace(/^JOURNAL_/, '') as JournalRejectionCode, 0, 'm')));
  }
  add(transportError(new JournalPayloadMissingError('ref', 'missing')));
  add(transportError(new SessionErasedError()));
  for (const reason of UNAUTHORIZED) {
    add(harnessUnauthorized(new HarnessUnauthorizedError(reason)));
  }
  // QA7-7: the public refusals the frontend and the worker key on.
  for (const code of codesOf('decision')) {
    add(publicTransportError(new HarnessDecisionRefusedError(code as DecisionRefusal, 'm')));
  }
  for (const code of codesOf('memory')) {
    add(publicTransportError(new HarnessMemoryRefusedError(code as MemoryRefusalCode, 'm')));
  }
  for (const code of [...codesOf('workspace'), 'WORKSPACE_CONFLICT']) {
    add(workspaceRefusal(new WorkspaceError(code as WorkspaceErrorCode, 'm')));
  }
  // ERR-UX (QA-7 open item, QA21-F4): every other refusal a person can see.
  add(transportError(new ConversationDeletedError()));
  add(publicTransportError(new HarnessQuotaExceededError('org', 'active_runs', 5)));
  for (const code of codesOf('turn')) {
    add(publicTransportError(new HarnessSessionRoleRefusedError(code as TurnRefusal, 'm')));
  }
  for (const code of codesOf('model_access')) {
    add(publicTransportError(new HarnessModelAccessRefusedError(code.toLowerCase() as AccessRefusalCode, 'm', null)));
  }
  // G-41: the harness, an engine or a model the org lets only some groups use.
  for (const code of codesOf('group_access')) {
    add(publicTransportError(new HarnessGroupAccessRefusedError(code as GroupAccessCode)));
  }
  for (const code of codesOf('model_account')) {
    add(publicTransportError(new HarnessModelAccountRefusedError(code as ModelAccountRefusalCode, 'm')));
  }
  for (const code of codesOf('approvals')) {
    add(publicTransportError(new HarnessApprovalsRefusedError(code as ApprovalsRefusalCode, 'm')));
  }
  for (const code of codesOf('schedule')) {
    add(scheduleHttpError(new HarnessScheduleRefusedError(code as ScheduleRefusalCode, 'm')));
  }
  // TR-04: a webhook's size and rate refusals.
  for (const code of codesOf('trigger')) {
    add(scheduleHttpError(new HarnessTriggerRefusedError(code as ErrorCodeOf<'trigger'>, 'm', 1)));
  }
  // ERR-UX2: engine profiles and the activity panel's refusals.
  for (const code of codesOf('engine_profile')) {
    add(publicTransportError(new HarnessEngineProfileRefusedError({ code: code as EngineProfileRefusalCode, detail: 'd' })));
  }
  add(publicTransportError(new ActivityPanelDisabledError()));
  // ADR-OPS-01: a stopped turn and the Controls page's refusals.
  for (const code of codesOf('operator_control')) {
    add(publicTransportError(new HarnessOperatorControlRefusedError(code as OperatorControlCode)));
  }
  // CPD-6: a pin that could not be made under CONTROL_PLANE_PIN=on.
  add(publicTransportError(new HarnessRunConfigUnavailableError()));
  add(transportError(new RunNotCompletedError()));
  for (const code of codesOf('publication')) {
    add(publicationHttpError(new PublicationRefusedError(code.toLowerCase(), 'm', 418)));
  }
  return out;
}

describe('agent-harness contracts: the harness wire error catalogue (CPD-0)', () => {
  it('the generated constants are the catalogue', () => {
    expect(WIRE_PREFIX).to.equal(catalog.wirePrefix);
    expect(Object.keys(ERROR_CODES)).to.deep.equal(generated.map((c) => c.code));
    for (const entry of generated) {
      expect(ERROR_CODES[entry.code as ErrorCode].status, entry.code).to.equal(entry.status);
    }
  });

  it('every code the internal transport answers is in the catalogue, at its status', () => {
    const answers = sent();
    expect(answers.size).to.be.greaterThan(40);
    for (const [code, status] of answers) {
      const entry = catalog.codes.find((c) => c.code === code);
      expect(entry, code).to.not.equal(undefined);
      expect(status, code).to.equal(entry!.status);
    }
  });

  it('the catalogue names no code Node never answers', () => {
    // The content route builds its two answers from the catalogue by name (harnessWireCode), so tsc holds them.
    const unanswered = catalog.codes
      .map((c) => c.code)
      .filter((code) => !sent().has(code));
    expect(unanswered.sort()).to.deep.equal(['CONTENT_MEDIA_TYPE', 'CONTENT_TOO_LARGE']);
  });
});
