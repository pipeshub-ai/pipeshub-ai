import { appName } from './app-name';
import type { Words } from './words';

/**
 * An action a run record or a job's history names (UI-B5, ADR-WF-01 D15), in the reader's language. The server sends
 * `actionKey` (the tool's own name, its verb and its app's key) beside the English `action` and `app`: a name this
 * table knows is worded per locale, then its verb, and only then the English, which stays the fallback for every
 * tool nobody has worded yet.
 */

export interface ActionKey {
  name: string;
  verb: string | null;
  app: string | null;
}

export interface ActionFacts {
  action?: string;
  app?: string;
  actionKey?: ActionKey;
}

/** Tool names worded in every locale (`ui.actionKey.name.*`). */
export const ACTION_NAMES: ReadonlySet<string> = new Set([
  'send_message',
  'post_message',
  'send_email',
  'reply_to_email',
  'create_draft',
  'create_issue',
  'update_issue',
  'create_ticket',
  'create_page',
  'update_page',
  'create_pull_request',
  'add_comment',
  'create_event',
  'create_task',
  'create_record',
  'update_record',
  'upload_file',
]);

/** Verbs worded in every locale (`ui.actionKey.verb.*`), for a tool whose own name is not in the table. */
export const ACTION_VERBS: ReadonlySet<string> = new Set([
  'send',
  'post',
  'reply',
  'create',
  'add',
  'update',
  'edit',
  'delete',
  'remove',
  'comment',
  'upload',
  'draft',
  'assign',
  'move',
  'close',
  'schedule',
]);

/** The action alone ("Send message"), or undefined when neither the keys nor the English name one. */
export function actionName(facts: ActionFacts, t: Words): string | undefined {
  const key = facts.actionKey;
  if (key && ACTION_NAMES.has(key.name)) return t(`ui.actionKey.name.${key.name}`);
  if (key?.verb && ACTION_VERBS.has(key.verb)) return t(`ui.actionKey.verb.${key.verb}`);
  return facts.action || undefined;
}

/** The app it acts in: the English display name when the record has one, else the app's key as a name. */
export function actionApp(facts: ActionFacts): string | undefined {
  return facts.app || (facts.actionKey?.app ? appName(facts.actionKey.app) : undefined);
}

/** One step a run took, in the reader's language: "Send message in Slack". */
export const stepWords = (facts: ActionFacts, t: Words): string => {
  const action = actionName(facts, t);
  const app = actionApp(facts);
  return action ? (app ? t('ui.workflows.summary.inApp', { what: action, app }) : action) : t('ui.workflows.summary.aStep');
};
