import type { TFunction } from 'i18next';
import { approvalAskRules } from '@/config';
import { mayApproveCard } from '../../approval-authority';
import { interactionAction, interactionTitle, isPlanApproval, isSiteUpload } from '../../interaction-wording';
import { isOpenApproval } from '../../interactions';
import type { DecisionAuthority, PendingApproval } from '../../types';
import type { JobAskItem } from './job-asks';

/**
 * What the compact floating card offers for an ask (BGUI-9, design `needs-you-card.tsx`): the action in plain words,
 * and either the decision itself, named against the safe alternative ("Allow once" / "Keep it out"), or one button
 * that opens the full card, for an ask whose answer needs more than a yes or a no.
 */
export type AskOffer =
  | {
      kind: 'decide';
      /** The plain line under the headline: "Create ticket in E2Esaas?". */
      action: string;
      allow: { text: string; name: string };
      keep: { text: string; name: string };
      /** The calls one click decides: the ask's own, or every pending call of a batch ("Allow all 3"). */
      cards: PendingApproval[];
      sending: boolean;
      /** The server refused an earlier answer (machine code): the card says so under the action. */
      errorCode: string | null;
    }
  | {
      kind: 'open';
      action: string;
      /** The button that opens the full card: Sign in, Connect, or Review. */
      primary: string;
      errorCode: string | null;
    };

/**
 * Only an ordinary approval the viewer may both allow and deny is decided on the compact card. A sign-in needs a code
 * typed, a connect its own sign-in window, a plan its steps read, a call of unknown effect a choice of three, and an
 * ask a rule decides its waiting line: each opens the full card.
 */
export function askOffer(t: TFunction, item: JobAskItem, authority: DecisionAuthority | undefined): AskOffer {
  if (item.kind === 'signin') {
    return { kind: 'open', action: t('engineSignIn.card.title'), primary: t('jobAsk.signInAction'), errorCode: null };
  }
  const open = item.group.items.filter(isOpenApproval);
  const items = open.length > 0 ? open : item.group.items;
  const [first] = items;
  const n = items.length;
  const errorCode = items.find((a) => a.errorCode)?.errorCode ?? null;
  if (first.kind === 'connect') {
    const what = first.connect?.item ?? '';
    return { kind: 'open', action: t('connectCard.title', { item: what }), primary: t('jobAsk.connectAction'), errorCode };
  }
  const action = interactionTitle(t, first, n);
  const decidable =
    first.kind !== 'unknown_effect' &&
    !(n === 1 && isPlanApproval(first)) &&
    items.every((a) => !approvalAskRules.holds(a.reason) && mayApproveCard(authority, a)) &&
    (authority?.deny ?? true);
  if (!decidable) return { kind: 'open', action, primary: t('jobAsk.review'), errorCode };
  const subject = interactionAction(t, first, n);
  const allowText = n > 1 ? t('interaction.allowAll', { count: n }) : isSiteUpload(first) ? t('approvalCard.siteUpload.allowRun') : t('approvalCard.flow.allowOnce');
  const keepText = n > 1 ? t('jobAsk.keepOutAll') : t('jobAsk.keepOut');
  // The name starts with the words on the button, so a voice user can say what they see (WCAG 2.5.3).
  const named = (text: string) => t('approvalCard.flow.buttonName', { action: text, subject });
  return {
    kind: 'decide',
    action,
    allow: { text: allowText, name: named(allowText) },
    keep: { text: keepText, name: named(keepText) },
    cards: open.filter((a) => a.status === 'pending'),
    sending: open.some((a) => a.status === 'sending'),
    errorCode,
  };
}
