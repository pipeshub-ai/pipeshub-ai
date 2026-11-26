/** A journal event's document as every store keeps it: no driver. */
import type {
  EventWriter,
  JournalVisibility,
  PayloadRef,
} from '../../../../modules/agent-harness/domain/journal';
import type { SealedContent } from '../../../../modules/agent-harness/domain/data-protection';

export interface SessionEventDocument {
  orgId: string;
  sessionId: string;
  streamId: string;
  seq: number;
  runId?: string;
  attemptId?: string;
  fence?: number;
  type: string;
  schemaVersion: number;
  critical: boolean;
  ts: Date;
  writer: EventWriter;
  labels?: Record<string, unknown>;
  data?: Record<string, unknown>;
  payloadRef?: PayloadRef;
  sealed?: SealedContent;
  visibility: JournalVisibility;
  /** E7: the session was erased; only the envelope is left. */
  erased?: boolean;
  /** E7: how many bytes of sealed content the tombstone replaced. */
  contentBytes?: number;
}
