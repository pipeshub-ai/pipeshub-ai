import { forEnumeratedOrg } from '../../../libs/context/orgContext';
import {
  changedPaths,
  orgScope,
  type ConfigChange,
} from '../domain/config-change';
import type { Grant, Installation, SubjectRef } from '../domain/objects';
import { ControlPlaneStoreError } from '../domain/store-errors';
import {
  CAPABILITY_MIGRATION_ID,
  type CapabilityLedger,
  type LegacyCapabilitySource,
  type LegacyPageOptions,
} from '../ports/capability-ledger';
import type { GrantStore } from '../ports/grant-store';
import type { InstallationStore } from '../ports/installation-store';
import type { ControlPlaneLog } from '../ports/log';
import type {
  CapabilityRepair,
  CapabilitySync,
  OrgReport,
} from '../ports/capability-sync';
import type {
  ControlPlaneDataMigration,
  MigrationMode,
} from '../ports/data-migration';
import type { ControlPlaneTransaction, CpTx } from '../ports/transaction';
import type { BindingService } from './binding-service';
import {
  planCapabilities,
  samePlanned,
  type LegacyAgent,
  type LegacyMcpInstance,
  type LegacyToolsetInstance,
  type ReleaseItems,
} from './capability-plan';

export { CAPABILITY_MIGRATION_ID };

export interface CapabilityMigrationDeps {
  readonly transaction: ControlPlaneTransaction;
  readonly installations: InstallationStore;
  readonly grants: GrantStore;
  readonly ledger: CapabilityLedger;
  readonly source: LegacyCapabilitySource;
  readonly release: ReleaseItems;
  readonly digest: (value: unknown) => string;
  readonly log: ControlPlaneLog;
  /** The orgs a whole run covers (every org of the deployment). */
  readonly orgs: () => Promise<string[]>;
  /**
   * Revokes an installation's credential bindings when it is removed, so an
   * installation re-created under the same id never inherits them. Null
   * where the host composes no credential vault.
   */
  readonly credentials: InstallationCredentials | null;
}

/** What removing an installation asks of its credentials: `BindingService` (CPD-8). */
export type InstallationCredentials = Pick<
  BindingService,
  'revokeInstallation'
>;

const SYSTEM_ACTOR = 'system';
/** Legacy instances live as these kinds; skills stay installed once an agent assigned them. */
const INSTANCE_KINDS = ['toolset', 'mcp'] as const;

interface Facts {
  toolsetInstances: LegacyToolsetInstance[];
  mcpInstances: LegacyMcpInstance[];
  agents: LegacyAgent[];
}

type Write =
  | {
      readonly kind: 'installation';
      readonly planned: Installation;
      readonly stored: Installation | null;
    }
  | {
      readonly kind: 'grant';
      readonly planned: Grant;
      readonly stored: Grant | null;
    };

/**
 * CPD-7's D2 migration of today's toolset instances, MCP instances and
 * manifests and agent edges into installations and grants (02-lld §11.2),
 * and the dual-write sync that keeps them current for one release. Every
 * object write is one control-plane transaction with its ledger entry, so it
 * is audited (`config.changed`) and revertible; a rerun writes nothing (MG-6).
 *
 * An org is recorded as migrated (CPD-10 R3) by a whole apply that neither
 * failed nor held anything back, as its last write, and unrecorded by revert,
 * as its first: while the row stands the control plane holds everything the
 * legacy stores grant. The dual-write sync covers one agent or the instances,
 * never a whole org, so it records nothing.
 */
export class CapabilityMigration
  implements
    CapabilitySync,
    CapabilityRepair,
    ControlPlaneDataMigration<OrgReport[]>
{
  readonly id = CAPABILITY_MIGRATION_ID;
  /** Per written object, not per org: the dual-write sync appends to it between runs. */
  readonly ledgerKey = `cpCapabilityLedger/${CAPABILITY_MIGRATION_ID}`;

  constructor(private readonly deps: CapabilityMigrationDeps) {}

  async run(mode: MigrationMode): Promise<OrgReport[]> {
    return this.runFor(mode, await this.deps.orgs());
  }

  dryRun(): Promise<OrgReport[]> {
    return this.run('dry-run');
  }

  apply(): Promise<OrgReport[]> {
    return this.run('apply');
  }

  revert(): Promise<OrgReport[]> {
    return this.run('revert');
  }

  /** The same, for the orgs named (an operator's `--org`). */
  async runFor(
    mode: MigrationMode,
    orgIds: readonly string[],
  ): Promise<OrgReport[]> {
    const reports: OrgReport[] = [];
    for (const orgId of orgIds) {
      // Each org runs as that org: the listing or the operator's `--org` names it.
      reports.push(
        await forEnumeratedOrg(orgId, 'control-plane.migration', () =>
          mode === 'revert' ? this.revertOrg(orgId) : this.migrate(orgId, mode),
        ),
      );
    }
    return reports;
  }

  /** Dual write: the org's instances (after a toolset or MCP instance changed). */
  syncInstallations(orgId: string): Promise<OrgReport> {
    return this.migrate(orgId, 'apply', { after: null, agents: false });
  }

  /** Dual write: one agent's grant (after the agent was created, edited or deleted). */
  syncAgent(orgId: string, agentKey: string): Promise<OrgReport> {
    return this.migrate(orgId, 'apply', { after: null, agentKey });
  }

  /**
   * A whole apply for an org recorded as migrated, so writes its dual-write sync skipped (the org's harness off,
   * ADR-OPS-08) are caught up; null for an org never recorded, whose authority is still the legacy pins and whose
   * first apply stays the operator's.
   */
  repair(orgId: string): Promise<OrgReport | null> {
    return forEnumeratedOrg(orgId, 'control-plane.migration', async () =>
      (await this.deps.ledger.migrated(orgId))
        ? this.migrate(orgId, 'apply')
        : null,
    );
  }

  private async migrate(
    orgId: string,
    mode: 'dry-run' | 'apply',
    scope: LegacyPageOptions = { after: null },
  ): Promise<OrgReport> {
    const report = emptyReport(orgId, mode);
    try {
      const facts = await this.read(orgId, scope);
      const plan = planCapabilities({
        orgId,
        ...facts,
        release: this.deps.release,
        digest: this.deps.digest,
      });
      report.holds.push(...plan.holds);
      const writes = await this.writesFor(
        orgId,
        plan.installations,
        plan.grants,
        scope,
        facts,
      );
      for (const write of writes) {
        if (
          write.stored !== null &&
          samePlanned<Installation | Grant>(
            write.stored,
            write.planned,
            this.deps.digest,
          )
        ) {
          report.unchanged += 1;
          continue;
        }
        if (write.stored === null) report.created += 1;
        else if (
          write.kind === 'installation' &&
          write.planned.status === 'removed'
        )
          report.removed += 1;
        else report.updated += 1;
        if (mode === 'apply') await this.applyWrite(orgId, write, report);
      }
      if (mode === 'apply' && wholeOrg(scope) && report.holds.length === 0)
        await this.recordMigrated(orgId, true);
    } catch (error) {
      report.failed = reasonOf(error);
      this.deps.log.warn('control_plane.capability_migration_failed', {
        orgId,
        mode,
        reasonCode: report.failed,
      });
    }
    return this.ended(report, wholeOrg(scope));
  }

  /** The report, logged once; a whole org's with its row as the run left it. */
  private async ended(report: OrgReport, whole = true): Promise<OrgReport> {
    try {
      if (whole)
        report.migrated = await this.deps.ledger.migrated(report.orgId);
    } catch (error) {
      report.failed ??= reasonOf(error);
    }
    this.deps.log.info('control_plane.capability_migration', summary(report));
    return report;
  }

  /**
   * One audited write that moves the org head, so every cached snapshot
   * reloads with the new authority; nothing when the row already says so.
   */
  private async recordMigrated(
    orgId: string,
    migrated: boolean,
  ): Promise<void> {
    if ((await this.deps.ledger.migrated(orgId)) === migrated) return;
    await this.deps.transaction.run(
      { orgId, actor: SYSTEM_ACTOR },
      async (tx) => {
        if (migrated) await this.deps.ledger.markMigrated(tx);
        else await this.deps.ledger.unmarkMigrated(tx);
        return {
          result: null,
          change: {
            scope: orgScope(orgId),
            object: {
              type: 'capability-migration',
              id: CAPABILITY_MIGRATION_ID,
            },
            revisionFrom: null,
            revisionTo: null,
            changeDigest: this.deps.digest({ migrated }),
            changed: ['migrated'],
            // Unrecording hands authority back to the legacy pins, which may grant more than the set did.
            narrowing: migrated,
          },
        };
      },
    );
  }

  private async read(orgId: string, scope: LegacyPageOptions): Promise<Facts> {
    const facts: Facts = { toolsetInstances: [], mcpInstances: [], agents: [] };
    let after = scope.after;
    for (;;) {
      const page = await this.deps.source.page(orgId, { ...scope, after });
      if (page.orgId !== orgId)
        throw new LegacySourceRefused(
          'the legacy source answered for another org',
        );
      facts.toolsetInstances.push(...page.toolsetInstances);
      facts.mcpInstances.push(...page.mcpInstances);
      facts.agents.push(...page.agents);
      if (page.nextAgent === null || scope.agentKey !== undefined) return facts;
      after = page.nextAgent;
    }
  }

  /** What each planned object needs, plus the removal of what the legacy stores no longer hold. */
  private async writesFor(
    orgId: string,
    installations: readonly Installation[],
    grants: readonly Grant[],
    scope: LegacyPageOptions,
    facts: Facts,
  ): Promise<Write[]> {
    const writes: Write[] = [];
    const instanceScope =
      scope.agentKey === undefined || scope.agents === false;
    const stored = new Map(
      (await this.deps.installations.listForOrg(orgId, {})).map((inst) => [
        inst.id,
        inst,
      ]),
    );
    for (const planned of installations) {
      if (!instanceScope && planned.kind !== 'skill') continue;
      writes.push({
        kind: 'installation',
        planned,
        stored: stored.get(planned.id) ?? null,
      });
    }
    if (instanceScope) {
      const planned = new Set(installations.map((inst) => inst.id));
      // Only what an earlier run wrote: another writer's installation is not the legacy stores' to remove.
      const ours = new Set(
        (await this.deps.ledger.entries(orgId))
          .filter((entry) => entry.object === 'installation')
          .map((entry) => entry.id),
      );
      for (const inst of stored.values()) {
        const legacyKind = (INSTANCE_KINDS as readonly string[]).includes(
          inst.kind,
        );
        if (
          legacyKind &&
          ours.has(inst.id) &&
          !planned.has(inst.id) &&
          inst.status !== 'removed'
        ) {
          writes.push({
            kind: 'installation',
            planned: { ...inst, status: 'removed' },
            stored: inst,
          });
        }
      }
    }
    if (scope.agents === false) return writes;
    const subjects: SubjectRef[] =
      scope.agentKey !== undefined
        ? [`agent:${scope.agentKey}`]
        : await this.ledgeredGrants(orgId, grants);
    const storedGrants = await this.deps.grants.getMany(orgId, [
      ...new Set([...subjects, ...grants.map((grant) => grant.subject)]),
    ]);
    const plannedSubjects = new Set(grants.map((grant) => grant.subject));
    for (const planned of grants) {
      writes.push({
        kind: 'grant',
        planned,
        stored: storedGrants.get(planned.subject) ?? null,
      });
    }
    const listed = new Set(
      facts.agents.map((agent) => `agent:${agent.agentKey}`),
    );
    for (const subject of subjects) {
      const current = storedGrants.get(subject);
      if (
        current === undefined ||
        plannedSubjects.has(subject) ||
        listed.has(subject)
      )
        continue;
      writes.push({
        kind: 'grant',
        planned: { ...current, entries: [] },
        stored: current,
      });
    }
    return writes;
  }

  /** The agent grants an earlier run wrote: an agent the legacy store no longer lists loses its grant. */
  private async ledgeredGrants(
    orgId: string,
    planned: readonly Grant[],
  ): Promise<SubjectRef[]> {
    const written = (await this.deps.ledger.entries(orgId))
      .filter((entry) => entry.object === 'grant')
      .map((entry) => entry.id);
    return [...new Set([...written, ...planned.map((grant) => grant.subject)])];
  }

  private async applyWrite(
    orgId: string,
    write: Write,
    report: OrgReport,
  ): Promise<void> {
    if (write.kind === 'installation' && write.planned.status === 'removed') {
      await this.revokeBindings(orgId, write.planned.id);
    }
    try {
      await this.deps.transaction.run(
        { orgId, actor: SYSTEM_ACTOR },
        async (tx) => ({
          result: null,
          change: await this.write(tx, write),
        }),
      );
    } catch (error) {
      if (
        !(error instanceof ControlPlaneStoreError) ||
        error.reason !== 'cp_invalid_object'
      )
        throw error;
      report.holds.push({
        reason: 'invalid_object',
        object: `${write.kind}:${idOf(write)}`,
      });
    }
  }

  private async write(tx: CpTx, write: Write): Promise<ConfigChange> {
    const from = write.stored?.revision ?? null;
    let to: number;
    if (write.kind === 'installation') {
      if (write.stored === null) {
        await this.deps.installations.create(tx, write.planned);
        to = 1;
      } else {
        to = await this.deps.installations.replace(
          tx,
          write.planned,
          write.stored.revision,
        );
      }
    } else {
      to = await this.deps.grants.put(
        tx,
        write.planned,
        write.stored?.revision ?? 0,
      );
    }
    await this.deps.ledger.record(tx, {
      object: write.kind,
      id: idOf(write),
      revision: to,
    });
    const { revision: _before, ...before } = (write.stored ?? {}) as Record<
      string,
      unknown
    >;
    const { revision: _after, ...after } = write.planned as unknown as Record<
      string,
      unknown
    >;
    return {
      scope: orgScope(tx.orgId),
      object: { type: write.kind, id: idOf(write) },
      revisionFrom: from,
      revisionTo: to,
      changeDigest: this.deps.digest(after),
      changed: changedPaths(write.stored === null ? null : before, after),
      narrowing: false,
    };
  }

  /**
   * Every live binding on the installation is revoked, each an audited write
   * of the binder, before the installation goes: a failure leaves it
   * installed and the next sync retries.
   */
  private async revokeBindings(
    orgId: string,
    installation: string,
  ): Promise<void> {
    await this.deps.credentials?.revokeInstallation(
      { orgId, actor: SYSTEM_ACTOR },
      installation,
    );
  }

  private async revertOrg(orgId: string): Promise<OrgReport> {
    const report = emptyReport(orgId, 'revert');
    try {
      await this.recordMigrated(orgId, false);
      for (const entry of await this.deps.ledger.entries(orgId)) {
        if (entry.object === 'installation') {
          await this.revokeBindings(orgId, entry.id);
        }
        try {
          await this.deps.transaction.run(
            { orgId, actor: SYSTEM_ACTOR },
            async (tx) => {
              if ((await this.deps.ledger.undo(tx, entry)) === 'drifted')
                throw new Drifted();
              return {
                result: null,
                change: {
                  scope: orgScope(orgId),
                  object: { type: entry.object, id: entry.id },
                  revisionFrom: entry.revision,
                  revisionTo: null,
                  changeDigest: this.deps.digest(null),
                  changed: [],
                  narrowing: true,
                },
              };
            },
          );
          report.reverted += 1;
        } catch (error) {
          if (!(error instanceof Drifted)) throw error;
          report.drifted.push(entry);
        }
      }
    } catch (error) {
      report.failed = reasonOf(error);
      this.deps.log.warn('control_plane.capability_migration_failed', {
        orgId,
        mode: 'revert',
        reasonCode: report.failed,
      });
    }
    return this.ended(report);
  }
}

/** An object changed since the ledger wrote it: revert keeps it (and writes nothing). */
class Drifted extends Error {}

export class LegacySourceRefused extends Error {
  readonly reason = 'legacy_source_refused';
}

const wholeOrg = (scope: LegacyPageOptions): boolean =>
  scope.agentKey === undefined && scope.agents !== false;

const idOf = (write: Write): string =>
  write.kind === 'installation' ? write.planned.id : write.planned.subject;

function emptyReport(orgId: string, mode: MigrationMode): OrgReport {
  return {
    orgId,
    mode,
    created: 0,
    updated: 0,
    unchanged: 0,
    removed: 0,
    reverted: 0,
    holds: [],
    drifted: [],
    failed: null,
    migrated: null,
  };
}

function reasonOf(error: unknown): string {
  const reason =
    (error as { reason?: unknown; code?: unknown } | null)?.reason ??
    (error as { code?: unknown } | null)?.code;
  return typeof reason === 'string' ? reason : 'legacy_source_unavailable';
}

const summary = (report: OrgReport): Record<string, unknown> => ({
  orgId: report.orgId,
  mode: report.mode,
  created: report.created,
  updated: report.updated,
  unchanged: report.unchanged,
  removed: report.removed,
  reverted: report.reverted,
  holds: report.holds.length,
  drifted: report.drifted.length,
  failed: report.failed,
  migrated: report.migrated,
});
