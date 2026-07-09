import { Model } from 'mongoose';
import { Logger } from '../../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../../libs/services/keyValueStore.service';
import { EncryptionService } from '../../../../libs/encryptor/encryptor';
import { configPaths } from '../../paths/paths';
import { Conversation } from '../../../enterprise_search/schema/conversation.schema';
import { AgentConversation } from '../../../enterprise_search/schema/agent.conversation.schema';
import {
  IAppliedFilterNode,
  IMessage,
} from '../../../enterprise_search/types/conversation.interfaces';

// Both models share the same messages/appliedFilters shape but are typed to
// different document interfaces — widen to `Model<any>` here since only the
// common `messages[].appliedFilters` fields are touched.
type ConversationModel = Model<any>;

interface KbAppsMigrationFlag {
  done?: boolean;
}

const MONGO_BATCH_SIZE = 100;

/** Exponential-backoff constants for waiting on the connector service's KB-apps migration. */
const KB_APPS_WAIT_MAX_ATTEMPTS = 10;
const KB_APPS_WAIT_INITIAL_DELAY_MS = 2_000;
const KB_APPS_WAIT_MAX_DELAY_MS = 30_000;

/**
 * One-time backfill that relocates stale Knowledge-Base filter chips stored on
 * past chat messages. Before the KB-recordGroup-to-app migration, a selected
 * Collection was tagged `nodeType: "recordGroup"` and stored in
 * `appliedFilters.kb`. Collections are now standalone `apps` documents, and
 * the current chat UI stores them in `appliedFilters.apps` with
 * `nodeType: "app"` — so old messages still carrying the legacy tag need to
 * move array membership to match, or reopening/regenerating from that
 * message resolves against the wrong bucket.
 *
 * Depends on the Python connector service's KB-apps migration
 * (`/migrations/kb_apps_v1`) having completed, since the ids only remain
 * valid once every legacy KB recordGroup has become an app document. Since
 * Node and Python start independently, this waits for that flag with
 * exponential backoff rather than checking once and giving up — mirroring
 * `ScheduledJobsBackfillMigration`. If the dependency never becomes ready in
 * that window, the completion flag is NOT written, so this migration is
 * retried in full on the next process restart.
 */
export class ChatKbFiltersMigration {
  private readonly backoffMaxAttempts: number;
  private readonly backoffInitialDelayMs: number;

  constructor(
    private readonly logger: Logger,
    private readonly kvStore: KeyValueStoreService,
    /**
     * Python's ConfigurationService (EncryptedKeyValueStore) encrypts every
     * key except an exact-match exclusion list that does NOT include
     * `/migrations/...` keys (that list only excludes `/services/migrations`
     * itself) — so `/migrations/kb_apps_v1` is stored AES-256-GCM encrypted.
     * These must match Python's SECRET_KEY-derived hex key exactly to decrypt it.
     */
    private readonly encryptionAlgorithm: string,
    private readonly encryptionSecretKey: string,
    backoffOptions: { maxAttempts?: number; initialDelayMs?: number } = {},
  ) {
    this.backoffMaxAttempts = backoffOptions.maxAttempts ?? KB_APPS_WAIT_MAX_ATTEMPTS;
    this.backoffInitialDelayMs =
      backoffOptions.initialDelayMs ?? KB_APPS_WAIT_INITIAL_DELAY_MS;
  }

  async run(): Promise<{
    conversationsUpdated: number;
    messagesUpdated: number;
    errored: number;
  }> {
    try {
      const flag = await this.kvStore.get<string>(configPaths.chatKbFiltersMigration);
      if (flag === 'true') {
        this.logger.info('Chat KB-filters migration already completed; skipping');
        return { conversationsUpdated: 0, messagesUpdated: 0, errored: 0 };
      }
    } catch (error) {
      this.logger.warn(
        'Failed to read chat KB-filters migration flag; proceeding with idempotent run',
        { error: error instanceof Error ? error.message : 'Unknown error' },
      );
    }

    const kbAppsMigrationDone = await this.waitForKbAppsMigrationWithBackoff();
    if (!kbAppsMigrationDone) {
      this.logger.warn(
        'KB-apps migration (connector service) has not completed within the retry window; ' +
          'chat KB-filters migration deferred to next startup (flag NOT set)',
      );
      return { conversationsUpdated: 0, messagesUpdated: 0, errored: 0 };
    }

    this.logger.info('Starting chat KB-filters migration');

    let conversationsUpdated = 0;
    let messagesUpdated = 0;
    let errored = 0;

    for (const Model of [Conversation, AgentConversation] as ConversationModel[]) {
      const result = await this.migrateModel(Model);
      conversationsUpdated += result.conversationsUpdated;
      messagesUpdated += result.messagesUpdated;
      errored += result.errored;
    }

    if (errored > 0) {
      this.logger.warn(
        'Chat KB-filters migration finished with errors — completion flag NOT written, will retry on next boot',
        { conversationsUpdated, messagesUpdated, errored },
      );
      return { conversationsUpdated, messagesUpdated, errored };
    }

    try {
      await this.kvStore.set(configPaths.chatKbFiltersMigration, 'true');
      this.logger.info('✅ Chat KB-filters migration finished', {
        conversationsUpdated,
        messagesUpdated,
      });
    } catch (error) {
      this.logger.error(
        'Failed to persist chat KB-filters migration completion flag; migration will re-run on next boot',
        { error: error instanceof Error ? error.message : 'Unknown error' },
      );
    }

    return { conversationsUpdated, messagesUpdated, errored };
  }

  private async migrateModel(
    Model: ConversationModel,
  ): Promise<{ conversationsUpdated: number; messagesUpdated: number; errored: number }> {
    let conversationsUpdated = 0;
    let messagesUpdated = 0;
    let errored = 0;

    // Only scan conversations that actually contain a legacy-tagged entry —
    // avoids a full collection scan for orgs with no stale KB filters.
    const cursor = Model.find({
      'messages.appliedFilters.kb.nodeType': 'recordGroup',
    }).cursor({ batchSize: MONGO_BATCH_SIZE });

    for await (const doc of cursor) {
      try {
        let changed = false;

        for (const message of doc.messages as IMessage[]) {
          const appliedFilters = message.appliedFilters;
          const kb = appliedFilters?.kb;
          if (!kb || kb.length === 0) {
            continue;
          }

          const staleEntries = kb.filter(
            (entry: IAppliedFilterNode) => entry?.nodeType === 'recordGroup',
          );
          if (staleEntries.length === 0) {
            continue;
          }

          appliedFilters.kb = kb.filter(
            (entry: IAppliedFilterNode) => entry?.nodeType !== 'recordGroup',
          );
          appliedFilters.apps = [
            ...(appliedFilters.apps || []),
            ...staleEntries.map((entry: IAppliedFilterNode) => ({
              id: entry.id,
              name: entry.name,
              nodeType: 'app',
              connector: entry.connector,
            })),
          ];
          messagesUpdated++;
          changed = true;
        }

        if (changed) {
          // appliedFilters is a schema-less nested object, not a sub-schema —
          // mark explicitly so Mongoose persists the deep array reassignment.
          doc.markModified('messages');
          await doc.save();
          conversationsUpdated++;
        }
      } catch (err) {
        errored++;
        this.logger.error('Failed to migrate chat KB filters for a conversation', {
          conversationId: doc._id?.toString(),
          error: err instanceof Error ? err.message : String(err),
        });
      }
    }

    return { conversationsUpdated, messagesUpdated, errored };
  }

  /**
   * Poll the Python connector service's KB-apps migration completion flag
   * with exponential backoff, so a Node boot that races ahead of the
   * connector service still gets a fair window to see it finish.
   */
  private async waitForKbAppsMigrationWithBackoff(): Promise<boolean> {
    let delay = this.backoffInitialDelayMs;

    for (let attempt = 1; attempt <= this.backoffMaxAttempts; attempt++) {
      try {
        // Python's raw Redis store JSON-encodes every string value it's given
        // (even an already-encrypted one) via json.dumps(), so the bytes here
        // are the ciphertext wrapped in an extra layer of JSON quoting — strip
        // that first. Then decrypt (AES-256-GCM, "iv:ciphertext:authTag" hex
        // format, set by Python's EncryptedKeyValueStore) with the same
        // SECRET_KEY-derived key Python uses, then JSON.parse the plaintext
        // to get { done, ... }.
        const raw = await this.kvStore.get<string>(configPaths.kbAppsMigrationDone);
        let flag: KbAppsMigrationFlag | null = null;
        if (raw) {
          const encryptedValue: string = JSON.parse(raw);
          const decrypted = EncryptionService.getInstance(
            this.encryptionAlgorithm,
            this.encryptionSecretKey,
          ).decrypt(encryptedValue);
          flag = JSON.parse(decrypted);
        }
        if (flag?.done === true) {
          this.logger.info('KB-apps migration confirmed complete', { attempt });
          return true;
        }
      } catch (error) {
        this.logger.warn('Failed to read KB-apps migration flag; retrying with backoff', {
          attempt,
          error: error instanceof Error ? error.message : String(error),
        });
      }

      const isLastAttempt = attempt === this.backoffMaxAttempts;
      if (!isLastAttempt) {
        this.logger.info('KB-apps migration not yet complete; retrying with backoff', {
          attempt,
          maxAttempts: this.backoffMaxAttempts,
          retryInMs: delay,
        });
        await new Promise<void>((resolve) => setTimeout(resolve, delay));
        delay = Math.min(delay * 2, KB_APPS_WAIT_MAX_DELAY_MS);
      }
    }

    return false;
  }
}
