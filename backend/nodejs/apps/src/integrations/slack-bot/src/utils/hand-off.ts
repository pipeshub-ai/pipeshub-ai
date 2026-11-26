import { toolActivityLabel } from './tool-display';
import type { SlackHandOff } from './agui-stream';
import { escapeSlackText } from './slack-escape';
import { displaySafe } from '../../../../modules/agent-harness/domain/display-safe';

/**
 * A tool's name is a third party's text: shown display-safe (ADR-TEXT-01; a journal from an older worker carries it
 * unmarked), with no emphasis of its own and no Slack token (SEC-B-03).
 */
function plain(text: string): string {
  return escapeSlackText(displaySafe(text).replace(/[*_~`]/g, ''));
}

/**
 * ADR-OWD-09 D-SLACK-ASK: what the thread reads when the run stops for an approval. Slack cannot show the card and
 * nobody decides from Slack: the link opens the conversation in PipesHub, behind its own sign-in, where the person (or
 * whoever may decide, as the card there says) approves or declines; the answer comes back to this thread. A message
 * sent to the thread meanwhile is queued on the run (D-SLACK-ASK 5) and the reply says so.
 */
export function handOffText(
  handOff: SlackHandOff,
  frontendBaseUrl: string,
): string {
  const what = handOff.waitingOn
    .map((call) => plain(toolActivityLabel(call.toolName, call.displayName)))
    .filter((w) => w !== '');
  const named = what.length === 0 ? '' : `: *${what.join('*, *')}*`;
  const path =
    handOff.link ??
    `/chat/?conversationId=${encodeURIComponent(handOff.conversationId)}`;
  const open = `<${frontendBaseUrl}${path}|Open this conversation in PipesHub> to approve or decline it.`;
  if (handOff.queued === true) {
    // R35SLACK-FU-3: a reply while the run is parked is queued, not lost and not answered yet.
    return [
      `This is still waiting for an approval${named}.`,
      `${open} I've added your message; it will be read once this goes on, and I'll post the answer here.`,
    ].join('\n');
  }
  return [
    `This needs an approval before I can go on${named}.`,
    `${open} I'll post the answer here once it goes on.`,
  ].join('\n');
}
