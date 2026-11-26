'use client';

import type { ChatArtifact, SSEArtifactEvent } from './types';

// CSI and OSC sequences, then any other control character but newline and tab.
const ANSI_CSI = /\u001b\[[0-?]*[ -/]*[@-~]/g;
const ANSI_OSC = /\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)?/g;
const CONTROL = /[\u0000-\u0008\u000b-\u001f\u007f]/g;

/** Engine output as plain text: terminal escape sequences and control characters removed. */
export function plainTerminalText(text: string): string {
  return text.replace(/\r\n/g, '\n').replace(ANSI_OSC, '').replace(ANSI_CSI, '').replace(CONTROL, '');
}

export interface TerminalBounds {
  chars: number;
  lines: number;
}

/** Appends to the terminal and keeps its newest part within both bounds. */
export function appendTerminal(
  terminal: string,
  addition: string,
  bounds: TerminalBounds,
): { terminal: string; truncated: boolean } {
  let next = terminal + addition;
  let truncated = false;
  if (next.length > bounds.chars) {
    next = next.slice(next.length - bounds.chars);
    truncated = true;
  }
  // The newline that starts the newest `bounds.lines + 1` lines, found from the end: this runs on every output
  // event of a live command, and splitting the whole (up to 256 KiB) terminal each time was most of its cost (PF-17).
  let cut = next.length;
  for (let found = 0; found <= bounds.lines; found += 1) {
    cut = cut > 0 ? next.lastIndexOf('\n', cut - 1) : -1;
    if (cut < 0) break;
  }
  if (cut >= 0) {
    next = next.slice(cut + 1);
    truncated = true;
  }
  return { terminal: next, truncated };
}

/**
 * An artifact card arriving again replaces the card with the same id where
 * it stands; a discarded draft removes it.
 */
export function withArtifactCard(
  artifacts: ChatArtifact[],
  artifact: ChatArtifact,
  lifecycle: SSEArtifactEvent['lifecycle'],
): ChatArtifact[] {
  const at = artifacts.findIndex((a) => a.id === artifact.id);
  if (lifecycle === 'discarded') return at < 0 ? artifacts : artifacts.filter((_, i) => i !== at);
  const card: ChatArtifact = lifecycle === 'draft' ? { ...artifact, lifecycle: 'draft' } : artifact;
  return at < 0 ? [...artifacts, card] : artifacts.map((a, i) => (i === at ? card : a));
}

/** The copy key of a budget refusal (P6.5), or null for any other code. Literal keys, so `i18n:keys` checks them. */
export function budgetRefusalKey(code: string | null | undefined): string | null {
  switch (code) {
    case 'budget_exhausted':
      return 'durableRun.budget.exhausted';
    case 'budget_insufficient':
      return 'durableRun.budget.insufficient';
    case 'budget_unavailable':
      return 'durableRun.budget.unavailable';
    case 'budget_account_closed':
      return 'durableRun.budget.closed';
    case 'child_budget_exceeds_remaining':
    case 'CHILD_BUDGET_EXCEEDS_REMAINING':
      return 'durableRun.budget.childExceeds';
    default:
      return null;
  }
}
