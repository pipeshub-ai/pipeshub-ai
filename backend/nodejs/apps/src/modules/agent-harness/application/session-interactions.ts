import {
  pastedSecretKindsIn,
  redactPastedSecrets,
} from '../domain/pasted-secrets';
import { createHash } from 'node:crypto';
import { canonicalDigest } from '../contracts/canonical-json';
import {
  HarnessConflictError,
  HarnessDecisionRefusedError,
  HarnessNotFoundError,
} from '../domain/errors';
import {
  CommitReceipt,
  HarnessRun,
  InteractionChannel,
  InteractionDecision,
  InteractionScope,
  TERMINAL_RUN_STATUSES,
} from '../domain/execution';
import {
  approverOnlyRefusal,
  Decider,
  DecisionRefusal,
  connectRefusal,
  decisionRefusal,
  PersonDecision,
} from '../domain/interactions';
import { principalRefFor } from '../domain/run-spec';
import { SessionRole } from '../domain/session-roles';
import {
  actorOnlyApprovers,
  ApproverDirectory,
} from '../ports/approver-directory';
import {
  ChatSessionRepository,
  SessionExecutionScope,
} from '../ports/chat-session-repository';
import { SessionRoleLookup } from '../ports/session-roles';
import {
  type ApprovalRules,
  type ApprovalsCounted,
  type OrgStanding,
  type RuleRefusal,
  type ViewerApprovalRules,
  usualApprovalRules,
} from '../ports/approval-rules';
import type { CommitFence } from '../ports/commit-fence';
import type { RuleGrantTarget, ToolRuleWriter } from '../ports/tool-rules';
import type { ConversationGrantReach } from './conversation-grant-reach';
import { ExecutionService } from './execution-service';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import { InteractionService } from './interaction-service';
import { isUnknownEffectInteraction } from './unknown-effects';

export interface DecideInteraction extends SessionExecutionScope {
  runId: string;
  interactionId: string;
  decision: PersonDecision;
  idempotencyKey: string;
  channel: Extract<InteractionChannel, 'web' | 'api'>;
  /** An approval for the rest of the conversation, when the worker offered it (UX1). */
  scope?: InteractionScope;
  /** AP-05: why the person declined, told to the run; only with `denied`. */
  reason?: string;
  /**
   * AP-06: the arguments the person approves in place of the call's own, where the worker offered it; only the
   * person the run acts for may edit what it sends.
   */
  edits?: Record<string, unknown>;
  /**
   * Main's "Don't ask again" (BUGS-B): save the answer as the person's own
   * rule for the tool, where the worker offered it (`ruleGrant`). Not part of
   * the decision's command id: the run sees a one-time decision.
   */
  remember?: boolean;
  /** The decider's own credential, which saves their rule. */
  authorization?: string;
}

/** What a "Don't ask again" saves, and with whose credential (BUGS-B). */
interface RememberedRule {
  toolRules: ToolRuleWriter;
  authorization: string;
  target: RuleGrantTarget;
  interactionId: string;
}

/** DECIDE409: one decision on several interactions of the run ("Allow all N", "Deny all"), one request. */
export interface DecideInteractions extends Omit<
  DecideInteraction,
  'interactionId'
> {
  interactionIds: readonly string[];
}

/** How an interaction stands after the request; `alreadyDecided`: an earlier decision stands, this one changed nothing. */
export interface InteractionOutcome {
  interactionId: string;
  decision: InteractionDecision;
  alreadyDecided?: true;
  /** An ask a rule says needs several approvals, counted and still waiting for the rest: not approved yet. */
  approvals?: ApprovalsCounted;
}

export interface DecidedInteraction {
  /** Null when every interaction was already decided: nothing was written. */
  receipt: CommitReceipt | null;
  interaction: InteractionOutcome;
}

export interface DecidedInteractions {
  receipt: CommitReceipt | null;
  interactions: InteractionOutcome[];
}

/** Each pass narrows the batch to what is still open; a third finds nothing new to drop. */
const DECIDE_PASSES = 3;

const REFUSAL_MESSAGES: Partial<Record<DecisionRefusal, string>> = {
  APPROVER_REQUIRED: 'Only the person this run acts for may approve',
  NOT_ALLOWED_TO_DECIDE:
    'Only the person this run acts for, the session owner or an admin may deny',
  OPERATOR_REQUIRED:
    'An action whose outcome is unknown is not re-run from the conversation; say whether it happened',
  DECISION_NOT_APPLICABLE:
    'Only an action whose outcome is unknown can be marked as having happened',
  EDIT_NOT_ALLOWED:
    'Only the person this task runs for can change what it sends; approve or deny it as it is',
  CONNECT_OWNER_REQUIRED:
    'Only the person this task runs for can connect this; anyone else may only decline it',
};

const EDIT_HAS_SECRET_MESSAGE =
  'The changed arguments have what looks like a password, API key or token in them: take it out and save it as a credential instead';

const refusalOf = (
  code: DecisionRefusal,
  words?: ApproverDirectory['refusalWords'],
): RuleRefusal => ({
  code,
  message:
    words?.[code as keyof NonNullable<typeof words>] ??
    REFUSAL_MESSAGES[code] ??
    'You may not decide this',
});

const refusedError = ({ code, message }: RuleRefusal): Error =>
  new HarnessDecisionRefusedError(code, message);

/** What the conversation may not say of an interaction, whoever says it. */
function unknownEffectRefusal(
  command: Pick<DecideInteraction, 'decision' | 'interactionId'>,
): DecisionRefusal | null {
  const unknown = isUnknownEffectInteraction(command.interactionId);
  if (command.decision === 'answered' && !unknown)
    return 'DECISION_NOT_APPLICABLE';
  if (command.decision === 'approved' && unknown) return 'OPERATOR_REQUIRED';
  return null;
}

export interface SessionInteractionServiceOptions {
  executions: ExecutionService;
  interactions: InteractionService;
  sessions: ChatSessionRepository;
  roles?: SessionRoleLookup;
  approvers?: ApproverDirectory;
  logger?: HarnessLogger;
  /** Saves a remembered answer as the person's rule; absent: "Don't ask again" is refused. */
  toolRules?: ToolRuleWriter;
  /** Rules beyond the usual one some asks carry; absent: the usual rule decides every ask. */
  rules?: ApprovalRules;
  /** FLOWGRANT: takes a conversation grant to the conversation's other runs; absent, it holds where it was given. */
  grants?: Pick<ConversationGrantReach<unknown>, 'reach'> &
    Partial<Pick<ConversationGrantReach<unknown>, 'answerHeld'>>;
}

/**
 * A person's decision on one of a session's runs: who they are in the session
 * and the org decides whether they may make it (ARCHITECTURE 4.9); the run
 * then learns it through `resolveActive`.
 */
export class SessionInteractionService {
  private readonly executions: ExecutionService;
  private readonly interactions: InteractionService;
  private readonly sessions: ChatSessionRepository;
  private readonly roles: SessionRoleLookup | undefined;
  private readonly approvers: ApproverDirectory;
  private readonly logger: HarnessLogger;
  private readonly toolRules: ToolRuleWriter | undefined;
  private readonly rules: ApprovalRules;
  private readonly grants: SessionInteractionServiceOptions['grants'];

  constructor(options: SessionInteractionServiceOptions) {
    this.executions = options.executions;
    this.interactions = options.interactions;
    this.sessions = options.sessions;
    this.roles = options.roles;
    this.approvers = options.approvers ?? actorOnlyApprovers;
    this.logger = options.logger ?? silentHarnessLogger;
    this.toolRules = options.toolRules;
    this.rules = options.rules ?? usualApprovalRules;
    this.grants = options.grants;
  }

  /**
   * What the viewer may decide on this run's approvals, by the decision
   * route's own rule (U3: the card hides what they cannot do).
   */
  async authority(
    scope: SessionExecutionScope,
    runId: string,
  ): Promise<DecisionAuthority> {
    const { decider, rules } = await this.decider(scope, runId);
    // AP-06: only the person the run acts for may edit what it sends; the card offers Edit to them alone.
    return {
      ...authorityOf(decider),
      ...(rules !== null ? await rules.authority(decider) : {}),
      edit: decider.isRunActor,
    };
  }

  async decide(command: DecideInteraction): Promise<DecidedInteraction> {
    const { interactionId, ...rest } = command;
    const decided = await this.decideIn(
      { ...rest, interactionIds: [interactionId] },
      decisionCommandId(command),
    );
    return {
      receipt: decided.receipt,
      interaction: decided.interactions[0] as InteractionOutcome,
    };
  }

  /** DECIDE409: "Allow all N" is one request and one transaction, so the run wakes once to all of them. */
  decideAll(command: DecideInteractions): Promise<DecidedInteractions> {
    // AP-06: an edit changes one call, decided on its own card.
    if (command.edits !== undefined) {
      return Promise.reject(
        new HarnessConflictError(
          'EDIT_NOT_OFFERED',
          'An edit changes one call: approve it on its own card',
        ),
      );
    }
    // BUGS-B: "Don't ask again" saves one tool's rule from the card that offered it; a batch never does.
    if (command.remember === true) {
      return Promise.reject(
        new HarnessConflictError(
          'CONVERSATION_GRANT_NOT_OFFERED',
          "Don't ask again saves one tool's rule: decide that call on its own card",
        ),
      );
    }
    return this.decideIn(command, batchDecisionCommandId(command));
  }

  private async decideIn(
    command: DecideInteractions,
    commandId: string,
  ): Promise<DecidedInteractions> {
    const {
      decider,
      role,
      participant,
      run: subject,
      rules,
    } = await this.decider(command, command.runId);
    for (const interactionId of command.interactionIds) {
      const plain =
        unknownEffectRefusal({ decision: command.decision, interactionId }) ??
        connectRefusal(decider, command.decision, interactionId);
      const refusal =
        plain !== null
          ? refusalOf(plain)
          : refusalFor(
              rules,
              decider,
              command.decision,
              interactionId,
              this.approvers.refusalWords,
            );
      if (refusal === null) continue;
      this.logger.warn(
        'interaction.refused',
        harnessLogMeta('interaction.refused', {
          orgId: command.orgId,
          sessionId: command.sessionId,
          runId: command.runId,
          interactionId,
          decision: command.decision,
          reason: refusal.code,
          role,
        }),
      );
      throw refusedError(refusal);
    }
    // AP-06: what a run sends is its actor's; an approver approves or denies it as asked.
    if (command.edits !== undefined && !decider.isRunActor) {
      this.logger.warn(
        'interaction.refused',
        harnessLogMeta('interaction.refused', {
          orgId: command.orgId,
          sessionId: command.sessionId,
          runId: command.runId,
          interactionId: command.interactionIds[0] ?? null,
          decision: command.decision,
          reason: 'EDIT_NOT_ALLOWED',
          role,
        }),
      );
      throw refusedError(refusalOf('EDIT_NOT_ALLOWED'));
    }
    // SEC-CR-07: a pasted secret in an edit would be journaled and sent; refused, never masked into the call.
    const secretKinds =
      command.edits !== undefined ? pastedSecretKindsIn(command.edits) : [];
    if (secretKinds.length > 0) {
      this.logger.warn(
        'interaction.refused',
        harnessLogMeta('interaction.refused', {
          orgId: command.orgId,
          sessionId: command.sessionId,
          runId: command.runId,
          interactionId: command.interactionIds[0] ?? null,
          decision: command.decision,
          reason: 'EDIT_HAS_SECRET',
          secretKinds: [...new Set(secretKinds)],
          role,
        }),
      );
      throw new HarnessConflictError(
        'EDIT_HAS_SECRET',
        EDIT_HAS_SECRET_MESSAGE,
      );
    }
    const fenceFor = (ids: readonly string[]): CommitFence | undefined =>
      this.decisionFence(command, decider, participant, rules, ids);
    // An ask a rule says needs several approvals is counted, and resolved by the approval that completes it.
    const awaiting =
      rules !== null
        ? await rules.count(
            decider,
            command.decision,
            principalRefFor(command.actorId),
            command.interactionIds,
            fenceFor,
          )
        : new Map<string, ApprovalsCounted>();
    const deciding = command.interactionIds.filter((id) => !awaiting.has(id));
    const [single] = deciding;
    const remembered =
      command.remember === true && single !== undefined
        ? await this.ruleGrant(command, single, subject)
        : null;
    const fence = fenceFor(deciding);
    let earlier = new Map<string, InteractionDecision>();
    const outcomes = (): InteractionOutcome[] =>
      command.interactionIds.map((interactionId) => {
        const counted = awaiting.get(interactionId);
        if (counted !== undefined) {
          return {
            interactionId,
            decision: command.decision,
            approvals: { ...counted },
          };
        }
        const before = earlier.get(interactionId);
        return before !== undefined
          ? { interactionId, decision: before, alreadyDecided: true }
          : { interactionId, decision: command.decision };
      });
    for (let pass = 1; ; pass += 1) {
      const open = deciding.filter((id) => !earlier.has(id));
      if (open.length === 0) return { receipt: null, interactions: outcomes() };
      try {
        const resolving = {
          orgId: command.orgId,
          runId: command.runId,
          decision: command.decision,
          channel: command.channel,
          ...(command.scope !== undefined ? { scope: command.scope } : {}),
          // SEC-CR-07: every channel's reason (Slack, email links, batches), not only the routes that strip it first.
          ...(command.decision === 'denied' && command.reason !== undefined
            ? { reason: redactPastedSecrets(command.reason).text }
            : {}),
          ...(command.decision === 'approved' && command.edits !== undefined
            ? { edits: command.edits }
            : {}),
          by: principalRefFor(command.actorId),
          commandId,
          ...(fence !== undefined ? { fence } : {}),
        };
        const [only, ...more] = open;
        const receipt =
          only !== undefined && more.length === 0
            ? await this.interactions.resolve({
                ...resolving,
                interactionId: only,
              })
            : await this.interactions.resolveAll({
                ...resolving,
                interactionIds: open,
              });
        const decided = outcomes();
        if (remembered !== null && decided[0]?.alreadyDecided !== true) {
          await this.remember(command, remembered);
        }
        if (
          (command.scope === 'conversation' &&
            command.decision === 'approved') ||
          command.decision === 'denied'
        ) {
          await this.reachConversation(command, subject, open, commandId);
        }
        return { receipt, interactions: decided };
      } catch (error) {
        if (
          !(error instanceof HarnessConflictError) ||
          error.code !== 'INTERACTION_NOT_OPEN'
        )
          throw error;
        // Decided already (another tab, a double click, the other half of a batch): that decision stands.
        earlier = await this.executions.interactionDecisions(
          command.orgId,
          command.runId,
        );
        const stillOpen = open.filter((id) => !earlier.has(id));
        if (stillOpen.length === 0)
          return { receipt: null, interactions: outcomes() };
        if (stillOpen.length < open.length && pass < DECIDE_PASSES) continue;
        const run = await this.executions.get(command.orgId, command.runId);
        if (run !== null && TERMINAL_RUN_STATUSES.has(run.status)) {
          throw new HarnessConflictError(
            'RUN_ENDED',
            'The run has ended, so there is nothing left to decide',
          );
        }
        throw error;
      }
    }
  }

  /**
   * WP115-FU-2: the asks a worker commit just raised that a conversation grant the run already holds answers are
   * answered as that grant's person's approval for the conversation. Throws when it cannot tell, so the commit path
   * logs it and the asks stay with the person.
   */
  async answerHeldGrants(
    run: HarnessRun,
    interactionIds: readonly string[],
    commandId: string,
  ): Promise<number> {
    return this.grants?.answerHeld?.(run, interactionIds, commandId) ?? 0;
  }

  /**
   * FLOWGRANT, ADR-GRANT-01 D9: the grant or decline the person just gave reaches their other runs of the
   * conversation, and answers every open ask there it covers; another participant's runs are never touched. The
   * decision has committed: a failure here leaves those asks to the person.
   */
  private async reachConversation(
    command: DecideInteractions,
    run: HarnessRun,
    interactionIds: readonly string[],
    commandId: string,
  ): Promise<void> {
    if (this.grants === undefined) return;
    try {
      await this.grants.reach({
        decision: command.decision === 'denied' ? 'denied' : 'approved',
        orgId: run.orgId,
        sessionId: run.sessionId,
        runId: run.runId,
        interactionIds,
        by: principalRefFor(command.actorId),
        channel: command.channel,
        commandId,
      });
    } catch (error) {
      this.logger.warn(
        'interaction.grant_reach_failed',
        harnessLogMeta('interaction.grant_reach_failed', {
          orgId: run.orgId,
          sessionId: run.sessionId,
          runId: run.runId,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
    }
  }

  /**
   * The tool whose rule a "Don't ask again" saves: only where the worker
   * offered it (main's default rule for the person's own MCP tool, PW-094),
   * and only for the person the run acts for, since the rule is theirs.
   */
  private async ruleGrant(
    command: DecideInteractions,
    interactionId: string,
    run: HarnessRun,
  ): Promise<RememberedRule> {
    const { toolRules } = this;
    const { authorization } = command;
    const target =
      toolRules !== undefined &&
      authorization !== undefined &&
      run.actorId === command.actorId
        ? await this.executions.ruleGrantOffered(
            run.orgId,
            run.runId,
            interactionId,
          )
        : null;
    if (
      toolRules === undefined ||
      authorization === undefined ||
      target === null
    ) {
      throw new HarnessConflictError(
        'CONVERSATION_GRANT_NOT_OFFERED',
        'Only the person this task runs for can save a rule for this tool, and only where the card offers it',
      );
    }
    return { toolRules, authorization, target, interactionId };
  }

  /** Saved after the decision committed; a rule that cannot be saved never undoes the decision. */
  private async remember(
    command: DecideInteractions,
    { toolRules, authorization, target, interactionId }: RememberedRule,
  ): Promise<void> {
    const rule = command.decision === 'approved' ? 'allow' : 'block';
    const saved = await toolRules
      .remember(authorization, target, rule)
      .catch(() => false);
    this.logger.info(
      'interaction.rule_remembered',
      harnessLogMeta('interaction.rule_remembered', {
        orgId: command.orgId,
        runId: command.runId,
        interactionId,
        rule,
        saved,
      }),
    );
  }

  /**
   * QA21-F2: a decision that only the approver standing allows holds that
   * standing in its transaction; losing it there answers as losing it before
   * would have (not told the run exists, or the role's own refusal). The
   * standing an ask's rule stands on is held the same way; a batch holds
   * every standing one of its interactions needs.
   */
  private decisionFence(
    command: Pick<DecideInteraction, 'orgId' | 'actorId' | 'decision'>,
    decider: Decider,
    participant: boolean,
    rules: ViewerApprovalRules | null,
    interactionIds: readonly string[],
  ): CommitFence | undefined {
    const refusing = (refusal: RuleRefusal) => (): Error =>
      participant ? refusedError(refusal) : new HarnessNotFoundError();
    const fences: CommitFence[] = [];
    const without =
      interactionIds
        .map((id) =>
          approverOnlyRefusal(
            decider,
            command.decision,
            (who, decision) =>
              refusalFor(rules, who, decision, id, this.approvers.refusalWords)
                ?.code ?? null,
          ),
        )
        .find((refusal) => refusal !== null) ?? null;
    if (without !== null && this.approvers.approverFence !== undefined) {
      fences.push(
        this.approvers.approverFence(
          command.orgId,
          command.actorId,
          refusing(refusalOf(without, this.approvers.refusalWords)),
        ),
      );
    }
    const ruled = rules?.fence(
      decider,
      command.decision,
      interactionIds,
      refusing,
    );
    if (ruled !== undefined) fences.push(ruled);
    const [only, ...more] = fences;
    if (more.length === 0) return only;
    return {
      hold: async (session) => {
        for (const fence of fences) await fence.hold(session);
      },
    };
  }

  /**
   * The same rule for a run the viewer found in the approvals queue (P2.11e),
   * with `participant` so the queue shows arguments only to whoever sees the
   * conversation or may approve, and `approves`, whether the viewer may approve
   * each ask as the list shows it. `org` lets a caller look the viewer up once;
   * `explained`, the asks' reasons as the caller already read them.
   */
  async authorityFor(
    viewer: { orgId: string; actorId: string },
    run: HarnessRun,
    org?: OrgStanding,
    explained?: ReadonlyMap<string, unknown>,
  ): Promise<QueueAuthority> {
    if (run.orgId !== viewer.orgId || run.harnessMajor !== 2) {
      return { ...nobody, participant: false };
    }
    const scope: SessionExecutionScope = {
      orgId: viewer.orgId,
      actorId: viewer.actorId,
      sessionId: run.sessionId,
      ...(run.agentKey !== undefined && run.agentKey.length > 0
        ? { agentKey: run.agentKey }
        : {}),
    };
    const standing = org ?? (await this.standing(viewer));
    const rules = await this.viewerRules(scope, run, standing, explained);
    const decider = await this.deciderFor(scope, run, standing, rules);
    if (decider === null) {
      return { ...nobody, participant: false };
    }
    return {
      ...authorityOf(decider.decider),
      ...(rules !== null
        ? {
            ...(await rules.authority(decider.decider)),
            approves: (interactionId: string) =>
              rules.approves(decider.decider, interactionId),
          }
        : {}),
      participant: decider.participant,
    };
  }

  /** Whether the viewer is an approver or an admin of their org; one lookup per queue read. */
  async standing(viewer: {
    orgId: string;
    actorId: string;
  }): Promise<OrgStanding> {
    const [isApprover, isOrgAdmin] = await Promise.all([
      this.approvers.isApprover(viewer.orgId, viewer.actorId),
      this.approvers.isOrgAdmin(viewer.orgId, viewer.actorId),
    ]);
    return { isApprover, isOrgAdmin };
  }

  /** Who the viewer is to this run: by role in its session, actor and org. */
  private async decider(
    scope: SessionExecutionScope,
    runId: string,
  ): Promise<{
    decider: Decider;
    role: SessionRole | null;
    participant: boolean;
    run: HarnessRun;
    rules: ViewerApprovalRules | null;
  }> {
    const run = await this.executions.get(scope.orgId, runId);
    if (run?.sessionId !== scope.sessionId || run.harnessMajor !== 2) {
      throw new HarnessNotFoundError();
    }
    const standing = await this.standing(scope);
    const rules = await this.viewerRules(scope, run, standing);
    const found = await this.deciderFor(scope, run, standing, rules);
    if (found === null) throw new HarnessNotFoundError();
    return { ...found, run, rules };
  }

  /** The rules the run's asks carry, as they apply to the viewer; null when none does. */
  private async viewerRules(
    viewer: { orgId: string; actorId: string },
    run: HarnessRun,
    standing: OrgStanding,
    explained?: ReadonlyMap<string, unknown>,
  ): Promise<ViewerApprovalRules | null> {
    const forRun = await this.rules.forRun(run, explained);
    return forRun === null ? null : forRun.forViewer(viewer, standing);
  }

  /**
   * A participant of the run's session decides by role; anyone else only as
   * an approver the bound directory names or an org admin (P2.6c), or where a
   * rule one of its asks carries names them; otherwise
   * they are not told the run exists (null).
   */
  private async deciderFor(
    scope: SessionExecutionScope,
    run: HarnessRun,
    standing: OrgStanding,
    rules: ViewerApprovalRules | null,
  ): Promise<{
    decider: Decider;
    role: SessionRole | null;
    participant: boolean;
  } | null> {
    const access = await this.sessions.findStreamAccess(scope);
    if (access === null) {
      const sameAgent =
        scope.agentKey === undefined || scope.agentKey === run.agentKey;
      if (
        !sameAgent ||
        (!standing.isApprover &&
          !standing.isOrgAdmin &&
          rules?.reaches !== true)
      ) {
        return null;
      }
    }
    const role: SessionRole | null =
      access === null
        ? null
        : this.roles !== undefined
          ? await this.roles.roleOf(scope)
          : access.audience === 'owner'
            ? 'owner'
            : 'viewer';
    return {
      decider: {
        isRunActor: run.actorId === scope.actorId,
        isApprover: standing.isApprover,
        role,
        isOrgAdmin: standing.isOrgAdmin,
      },
      role,
      participant: access !== null,
    };
  }
}

export interface DecisionAuthority {
  approve: boolean;
  deny: boolean;
  /** AP-06: the viewer may edit a call's arguments before approving it (the run's actor); only on the run's own read. */
  edit?: boolean;
  /** What the rules some of the run's asks carry add (`ViewerApprovalRules.authority`). */
  [field: string]: unknown;
}

/** The decision route's rule for one run of the approvals queue. */
export interface QueueAuthority extends DecisionAuthority {
  participant: boolean;
  /** Whether the viewer may approve the ask, where a rule decides it; absent or undefined: `approve`. */
  approves?(interactionId: string): boolean | undefined;
}

export type { OrgStanding };

const nobody = { approve: false, deny: false } as const;

/** The usual rule as the card shows it. */
function authorityOf(decider: Decider): DecisionAuthority {
  return {
    approve: decisionRefusal(decider, 'approved') === null,
    deny: decisionRefusal(decider, 'denied') === null,
  };
}

/** The refusal an ask's rule gives, or the usual rule's where none decides it. */
function refusalFor(
  rules: ViewerApprovalRules | null,
  decider: Decider,
  decision: PersonDecision,
  interactionId: string,
  words?: ApproverDirectory['refusalWords'],
): RuleRefusal | null {
  const ruled = rules?.refusal(decider, decision, interactionId);
  if (ruled !== undefined) return ruled;
  const usual = decisionRefusal(decider, decision);
  return usual === null ? null : refusalOf(usual, words);
}

/** One decision per person, interaction and idempotency key: a retried request replays it. */
export const decisionCommandId = (command: DecideInteraction): string =>
  `user-decision:${createHash('sha256')
    .update(
      [
        command.orgId,
        command.sessionId,
        command.runId,
        command.actorId,
        command.interactionId,
        command.decision,
        command.idempotencyKey,
        // Absent for a one-time decision, so its id is the one it had before UX1.
        ...(command.scope !== undefined ? [command.scope] : []),
        // AP-06: another edit is another decision; the first one stands.
        ...(command.edits !== undefined
          ? ['edits', canonicalDigest(command.edits)]
          : []),
      ].join('\0'),
    )
    .digest('hex')}`;

/** One batch per person, run, set of interactions and idempotency key: a retried "Allow all" replays it. */
export const batchDecisionCommandId = (command: DecideInteractions): string =>
  `user-decision:${createHash('sha256')
    .update(
      [
        'batch',
        command.orgId,
        command.sessionId,
        command.runId,
        command.actorId,
        ...[...command.interactionIds].sort(),
        command.decision,
        command.idempotencyKey,
        ...(command.scope !== undefined ? [command.scope] : []),
      ].join('\0'),
    )
    .digest('hex')}`;
