/**
 * Mail log repository: the audit record of each email the server sent.
 */
export interface MailLogEntry {
  orgId?: string;
  subject?: string;
  from: string;
  // Required by the store; a missing one fails the write, which the caller logs.
  to?: string[];
  cc?: string[];
  emailTemplateType: string;
}

export interface MailLogRepository {
  record(entry: MailLogEntry): Promise<void>;
}
