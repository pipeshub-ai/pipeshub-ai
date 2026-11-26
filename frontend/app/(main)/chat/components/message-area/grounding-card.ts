import type { TFunction } from 'i18next';
import { placesText } from '../../subject-not-found';
import type { AskUserQuestionPayload, GroundingCard } from '../../types';

/** A grounding question's typed fields (ADR-RUN-03 D1, FU-2), when the card carries whole ones. */
export function groundingCardOf(value: unknown): GroundingCard | null {
  if (value === null || typeof value !== 'object') return null;
  const raw = value as Record<string, unknown>;
  const { subject, useFound, nameIt } = raw;
  if (typeof subject !== 'string' || !subject || typeof useFound !== 'string' || typeof nameIt !== 'string') return null;
  const strings = (v: unknown) => (Array.isArray(v) ? v.filter((n): n is string => typeof n === 'string' && n !== '') : []);
  return { subject, near: strings(raw.near), useFound, nameIt, places: strings(raw.places) };
}

/**
 * How a grounding card reads in the reader's language. Only what the person sees changes: the reply still sends the
 * card's English question and labels, which the worker reads it by. A card without typed fields reads as sent.
 */
export interface CardWords {
  intent: (sent: string) => string;
  question: (sent: string) => string;
  header: (sent: string) => string;
  label: (sent: string) => string;
}

const AS_SENT: CardWords = { intent: (s) => s, question: (s) => s, header: (s) => s, label: (s) => s };

export function cardWords(payload: Pick<AskUserQuestionPayload, 'grounding'>, t: TFunction, locale?: string): CardWords {
  const card = groundingCardOf(payload.grounding);
  if (card === null) return AS_SENT;
  const subject = { subject: card.subject };
  // Connected sources searched too: the question names every place looked in (ANSWER-CONTRACT-SOURCES).
  const places = card.places.length > 0 ? placesText(card.places, t('chat.notFound.knowledge'), locale) : null;
  const asked = card.near.length > 0 ? 'questionNear' : 'questionNone';
  return {
    intent: () =>
      places !== null ? t('askUserQuestion.grounding.intentIn', { ...subject, places }) : t('askUserQuestion.grounding.intent', subject),
    question: () =>
      places !== null
        ? t(`askUserQuestion.grounding.${asked}In`, { ...subject, places })
        : t(`askUserQuestion.grounding.${asked}`, subject),
    header: () => t('askUserQuestion.grounding.header'),
    label: (sent) =>
      sent === card.useFound
        ? t('askUserQuestion.grounding.useFound')
        : sent === card.nameIt
          ? t('askUserQuestion.grounding.nameIt')
          : sent,
  };
}
