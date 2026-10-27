'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Checkbox,
  Flex,
  Heading,
  RadioGroup,
  Text,
  TextArea,
} from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type {
  AskUserQuestionAnswer,
  AskUserQuestionItem,
  AskUserQuestionOption,
  AskUserQuestionPayload,
  PendingAskUserQuestion,
} from '../../types';

const SOMETHING_ELSE_ID = '__something_else__';
const NO_PREFERENCE_ID = '__no_preference__';
const NO_PREFERENCE_LABEL = '[No preference]';
const MAX_TYPED_ANSWER_CHARS = 1000;

const SOMETHING_ELSE_OPTION: AskUserQuestionOption = {
  id: SOMETHING_ELSE_ID,
  label: 'Something else',
  isUserInput: true,
};

const CATCH_ALL_LABEL = /^(something else|other|none of the above|other option|enter your own|custom option|not listed|none of these)$/i;

function stripCatchAlls(options: AskUserQuestionOption[]): AskUserQuestionOption[] {
  return options.filter((o) => !CATCH_ALL_LABEL.test(o.label.trim()));
}

function stableId(prefix: string, text: string, index: number): string {
  const slug = text
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 48);
  return `${prefix}_${index}_${slug || 'item'}`;
}

function optionId(opt: AskUserQuestionOption | string): string {
  if (typeof opt === 'string') return opt;
  return opt.id || opt.label;
}

function optionLabel(opt: AskUserQuestionOption | string): string {
  return typeof opt === 'string' ? opt : opt.label;
}

function sameText(a: string | undefined, b: string | undefined): boolean {
  return (a ?? '').trim().toLowerCase() === (b ?? '').trim().toLowerCase();
}

export function normalizeAskUserQuestionPayload(
  raw: AskUserQuestionPayload,
): AskUserQuestionPayload {
  const questions = (raw.questions ?? []).map((q, qi) => {
    const question = typeof q.question === 'string' ? q.question : String(q.question ?? '');
    const options = (q.options ?? []).map((opt, oi) => {
      if (typeof opt === 'string') {
        return { id: stableId('opt', opt, oi), label: opt, isUserInput: false };
      }
      const label = typeof opt.label === 'string' ? opt.label : String(opt.label ?? '');
      const description = typeof opt.description === 'string' ? opt.description.trim() : '';
      return {
        id: opt.id || stableId('opt', label, oi),
        label,
        isUserInput: Boolean(opt.isUserInput),
        ...(description ? { description } : {}),
      };
    });
    const header = typeof q.header === 'string' ? q.header.trim() : '';
    return {
      uuid: q.uuid || stableId('q', question, qi),
      question,
      options,
      multiSelect: Boolean(q.multiSelect),
      ...(header ? { header } : {}),
    };
  });
  return {
    name: 'ask_user_question',
    ...(raw.userIntent ? { userIntent: raw.userIntent } : {}),
    ...(raw.expiresAt ? { expiresAt: raw.expiresAt } : {}),
    ...(raw.repliedInChat ? { repliedInChat: true } : {}),
    questions,
  };
}

/** Past `expiresAt` the paused turn is gone: the card is read-only and the user replies in the chat. */
export function isAskUserQuestionExpired(payload: AskUserQuestionPayload, now = Date.now()): boolean {
  const expiresAt = payload.expiresAt ? Date.parse(payload.expiresAt) : NaN;
  return Number.isFinite(expiresAt) && now >= expiresAt;
}

/** Two askings of one question differ in case, spacing or the closing mark. */
export function normalizeQuestionText(text: string): string {
  return text.toLowerCase().replace(/\s+/g, ' ').replace(/[\s?!.:]+$/, '').trim();
}

export function mergeAskUserQuestionPayloads(
  existing: AskUserQuestionPayload,
  incoming: AskUserQuestionPayload,
): AskUserQuestionPayload {
  const a = normalizeAskUserQuestionPayload(existing);
  const b = normalizeAskUserQuestionPayload(incoming);
  const seen = new Set(a.questions.map((q) => normalizeQuestionText(q.question)));
  const extra = b.questions.filter((q) => !seen.has(normalizeQuestionText(q.question)));
  const expiresAt = b.expiresAt ?? a.expiresAt;
  return {
    name: 'ask_user_question',
    userIntent: a.userIntent || b.userIntent,
    ...(expiresAt ? { expiresAt } : {}),
    questions: [...a.questions, ...extra],
  };
}

export function buildAnswerMessage(
  payload: AskUserQuestionPayload,
  answers: Record<string, AskUserQuestionAnswer>
): string {
  const questions = normalizeAskUserQuestionPayload(payload).questions;
  const lines = questions.map((q, i) => {
    const a = answerForQuestion(q, answers, questions);
    const parts = (a?.selectedOptionIds ?? [])
      .map((optId) => {
        if (optId === NO_PREFERENCE_ID) return NO_PREFERENCE_LABEL;
        if (optId === SOMETHING_ELSE_ID) {
          return a?.userInputs?.[optId]?.trim() ?? '';
        }
        const opt = q.options.find((o) => o.id === optId);
        const userText = a?.userInputs?.[optId]?.trim();
        return opt?.isUserInput && userText ? userText : (opt?.label ?? optId);
      })
      .filter(Boolean)
      .join(', ');
    return `${i + 1}. "${q.question}" → ${parts}`;
  });
  return `User selections:\n${lines.join('\n')}`;
}

function sameAnswer(a: AskUserQuestionAnswer, b: AskUserQuestionAnswer): boolean {
  const ids = (x: AskUserQuestionAnswer) => [...(x.selectedOptionIds ?? [])].sort();
  const left = ids(a);
  const right = ids(b);
  return (
    left.length === right.length &&
    left.every((id, i) => id === right[i]) &&
    left.every((id) => (a.userInputs?.[id] ?? '').trim() === (b.userInputs?.[id] ?? '').trim())
  );
}

/** Resume text for a submit: only questions the model has not been answered yet,
 *  or whose answer the user changed since. Everything is sent when nothing was sent before. */
export function buildResumeMessage(
  payload: AskUserQuestionPayload,
  answers: Record<string, AskUserQuestionAnswer>,
  sentAnswers?: Record<string, AskUserQuestionAnswer>,
): string {
  const normalized = normalizeAskUserQuestionPayload(payload);
  if (!sentAnswers || Object.keys(sentAnswers).length === 0) {
    return buildAnswerMessage(normalized, answers);
  }
  const questions = normalized.questions.filter((q) => {
    const sent = q.uuid ? sentAnswers[q.uuid] : undefined;
    const current = answerForQuestion(q, answers, normalized.questions);
    return !sent || !current || !sameAnswer(sent, current);
  });
  return buildAnswerMessage(
    { ...normalized, questions: questions.length > 0 ? questions : normalized.questions },
    answers,
  );
}

/** True when `text` is the synthetic follow-up query built by `buildAnswerMessage`. */
export function isAskUserQuestionResumeQuery(text: string | undefined | null): boolean {
  return typeof text === 'string' && text.trimStart().startsWith('User selections:');
}

const SELECTION_LINE = /^\s*\d+\.\s*["“](.+?)["”]\s*(?:→|->)\s*(.*)\s*$/;

function findQuestion(
  questions: AskUserQuestionItem[],
  questionText: string,
  fallbackIndex: number,
): AskUserQuestionItem | undefined {
  return (
    questions.find((item) => item.question === questionText) ??
    questions.find((item) => sameText(item.question, questionText)) ??
    questions[fallbackIndex]
  );
}

function findOption(
  options: Array<AskUserQuestionOption | string>,
  label: string,
): AskUserQuestionOption | string | undefined {
  return (
    options.find((o) => optionLabel(o) === label) ??
    options.find((o) => sameText(optionLabel(o), label)) ??
    options.find((o) => sameText(optionId(o), label))
  );
}

const LIST_SEPARATOR = ', ';

/** `buildAnswerMessage` joins picks with ", ", and labels or typed text may hold
 *  one too, so the line cannot simply be split. A single-select answer is one
 *  piece; a multi-select one is read left to right, taking the longest option
 *  label that fits and treating whatever lies between labels as typed text. */
function splitSelections(raw: string, q: AskUserQuestionItem): string[] {
  const text = raw.trim();
  if (!text) return [];
  if (!q.multiSelect) return [text];

  const known = [NO_PREFERENCE_LABEL, ...q.options.map((o) => optionLabel(o))]
    .filter(Boolean)
    .sort((a, b) => b.length - a.length);
  const lower = text.toLowerCase();
  const labelAt = (i: number): string | undefined =>
    known.find((label) => {
      const end = i + label.length;
      return (
        lower.startsWith(label.toLowerCase(), i) &&
        (end === text.length || text.startsWith(LIST_SEPARATOR, end))
      );
    });

  const parts: string[] = [];
  let i = 0;
  while (i < text.length) {
    const label = labelAt(i);
    if (label) {
      parts.push(text.slice(i, i + label.length));
      i += label.length + LIST_SEPARATOR.length;
      continue;
    }
    let next = text.indexOf(LIST_SEPARATOR, i);
    while (next >= 0 && !labelAt(next + LIST_SEPARATOR.length)) {
      next = text.indexOf(LIST_SEPARATOR, next + 1);
    }
    const end = next >= 0 ? next : text.length;
    parts.push(text.slice(i, end).trim());
    i = end + LIST_SEPARATOR.length;
  }
  return parts.filter(Boolean);
}

/** Inverse of `buildAnswerMessage` — recover structured answers from the saved resume query. */
export function parseAnswerMessage(
  text: string,
  payload: AskUserQuestionPayload,
): Record<string, AskUserQuestionAnswer> {
  const questions = normalizeAskUserQuestionPayload(payload).questions;
  const result: Record<string, AskUserQuestionAnswer> = {};
  if (!text) return result;
  for (const line of text.split('\n')) {
    const match = line.match(SELECTION_LINE);
    if (!match) continue;
    const questionText = match[1];
    const rawParts = match[2];
    const q = findQuestion(questions, questionText, Object.keys(result).length);
    if (!q?.uuid) continue;
    const selectedOptionIds: string[] = [];
    const userInputs: Record<string, string> = {};
    for (const label of splitSelections(rawParts, q)) {
      if (label === NO_PREFERENCE_LABEL) {
        selectedOptionIds.push(NO_PREFERENCE_ID);
        continue;
      }
      const opt = findOption(q.options, label);
      if (opt) {
        selectedOptionIds.push(optionId(opt));
      } else {
        selectedOptionIds.push(SOMETHING_ELSE_ID);
        userInputs[SOMETHING_ELSE_ID] = label;
      }
    }
    result[q.uuid] = { questionUuid: q.uuid, selectedOptionIds, userInputs };
  }
  return result;
}

function answerForQuestion(
  q: AskUserQuestionItem,
  answers: Record<string, AskUserQuestionAnswer>,
  siblings: AskUserQuestionItem[],
): AskUserQuestionAnswer | undefined {
  if (q.uuid && answers[q.uuid]) return answers[q.uuid];
  const values = Object.values(answers);
  const byUuid = values.find((a) => a.questionUuid === q.uuid);
  if (byUuid) return byUuid;
  // Option ids come from the label alone (`optionId` here, `"opt_" + label` in
  // intrim_tools.py), so two questions offering "Yes" share that option's id.
  // Matching on id/label is a last resort for answers built against another
  // payload; skipping the ones a sibling question owns keeps answering the
  // first question from silently answering the rest.
  const owned = new Set(
    siblings.filter((s) => s.uuid && s.uuid !== q.uuid).map((s) => s.uuid),
  );
  const labels = new Set(
    (q.options ?? []).map((o) => optionLabel(o).trim().toLowerCase()).filter(Boolean),
  );
  const ids = new Set((q.options ?? []).map((o) => optionId(o)));
  return values.find((a) =>
    !owned.has(a.questionUuid) &&
    (a.selectedOptionIds ?? []).some((id) => {
      if (id === SOMETHING_ELSE_ID || id === NO_PREFERENCE_ID) return false;
      if (ids.has(id)) return true;
      return labels.has(id.trim().toLowerCase());
    }),
  );
}

function isOptionSelected(selected: Set<string>, opt: AskUserQuestionOption | string): boolean {
  const id = optionId(opt);
  const label = optionLabel(opt);
  if (selected.has(id) || selected.has(label)) return true;
  for (const value of selected) {
    if (sameText(value, id) || sameText(value, label)) return true;
  }
  return false;
}

function answeredOptions(
  q: AskUserQuestionItem,
  a: AskUserQuestionAnswer | undefined,
  somethingElseLabel: string,
  noPreferenceLabel: string,
): Array<{ id: string; label: string; selected: boolean }> {
  const selected = new Set(a?.selectedOptionIds ?? []);
  const rows = stripCatchAlls(q.options).map((opt) => {
    const id = optionId(opt);
    const ut = a?.userInputs?.[id]?.trim();
    const base = optionLabel(opt);
    return {
      id,
      label: ut && typeof opt !== 'string' && opt.isUserInput ? `${base}: ${ut}` : base,
      selected: isOptionSelected(selected, opt),
    };
  });
  if (selected.has(SOMETHING_ELSE_ID)) {
    const custom = a?.userInputs?.[SOMETHING_ELSE_ID]?.trim();
    rows.push({
      id: SOMETHING_ELSE_ID,
      label: custom || somethingElseLabel,
      selected: true,
    });
  }
  if (selected.has(NO_PREFERENCE_ID)) {
    rows.push({ id: NO_PREFERENCE_ID, label: noPreferenceLabel, selected: true });
  }
  return rows;
}

function validateQuestion(
  q: AskUserQuestionItem,
  a: AskUserQuestionAnswer | undefined,
  extraOptions: AskUserQuestionOption[] = []
): boolean {
  const allOptions = [...q.options, ...extraOptions];
  const sel = a?.selectedOptionIds ?? [];
  if (q.multiSelect) {
    if (sel.length < 1) return false;
  } else if (sel.length !== 1) {
    return false;
  }
  for (const id of sel) {
    const opt = allOptions.find((o) => o.id === id);
    if (opt?.isUserInput) {
      const t = (a?.userInputs?.[id] ?? '').trim();
      if (!t) return false;
    }
  }
  return true;
}

function firstUnansweredStep(
  questions: AskUserQuestionItem[],
  answers: Record<string, AskUserQuestionAnswer>,
): number {
  const idx = questions.findIndex((q) =>
    !validateQuestion(q, answerForQuestion(q, answers, questions), [SOMETHING_ELSE_OPTION]),
  );
  return idx >= 0 ? idx : Math.max(0, questions.length - 1);
}

/** True while any question still lacks a usable answer. A resume can end by
 *  asking MORE questions (see `mergeAskUserQuestionPayloads`): the turn then has
 *  an answer even though the new question does not, so callers use this to keep
 *  such a card interactive instead of locking it as answered. */
export function hasUnansweredQuestions(
  payload: AskUserQuestionPayload,
  answers: Record<string, AskUserQuestionAnswer>,
): boolean {
  const questions = normalizeAskUserQuestionPayload(payload).questions;
  return questions.some(
    (q) => !validateQuestion(q, answerForQuestion(q, answers, questions), [SOMETHING_ELSE_OPTION]),
  );
}

/** Whether `pending`'s card belongs to this assistant row. Both ids are checked
 *  because `assistantMessageId` holds whichever the asking run had: the thread
 *  row id for a live send, the backend id for a regenerate (see
 *  `onAskUserQuestion`'s `rowId` in streaming.ts). Any slot-wide test here would
 *  hand one card to every assistant row in the thread. */
export function askUserQuestionOwnsRow(
  pending: PendingAskUserQuestion | null | undefined,
  rowKey: string | undefined,
  backendMessageId: string | undefined,
): boolean {
  if (!pending) return false;
  return (
    (Boolean(rowKey) && pending.assistantMessageId === rowKey) ||
    (Boolean(backendMessageId) && pending.assistantMessageId === backendMessageId)
  );
}

/** Whether a settled reply shows the "Stopped" marker. A row whose card is
 *  its whole output is left out, waiting or answered: Stop cut nothing short.
 *  That is a card row with no answer text, or one closed by chatting
 *  (`repliedInChat`), whose answer went to a new row. A stopped resume merges
 *  its partial answer onto the card row (`loadHistoricalMessages`), so a card
 *  row with answer text keeps the marker. */
export function showsStoppedMarker(row: {
  status: string | undefined;
  isStreaming: boolean;
  hasQuestionCard: boolean;
  hasAnswerText: boolean;
  repliedInChat: boolean;
}): boolean {
  if (row.isStreaming || row.status !== 'stopped') return false;
  return !(row.hasQuestionCard && (!row.hasAnswerText || row.repliedInChat));
}

function hasVisibleSelections(
  questions: AskUserQuestionItem[],
  answers: Record<string, AskUserQuestionAnswer>,
): boolean {
  return questions.some((q) => {
    const a = answerForQuestion(q, answers, questions);
    return (a?.selectedOptionIds?.length ?? 0) > 0;
  });
}

export interface AskUserQuestionCardProps {
  payload: AskUserQuestionPayload;
  initialAnswers: Record<string, AskUserQuestionAnswer>;
  status: 'pending' | 'submitted' | 'persisted';
  sentAnswers?: Record<string, AskUserQuestionAnswer>;
  onAnswersChange?: (answers: Record<string, AskUserQuestionAnswer>) => void;
  onSubmit?: (message: string, answers: Record<string, AskUserQuestionAnswer>) => void;
  /** Question to open on. Defaults to the first one without an answer. */
  initialStep?: number;
}

export function AskUserQuestionCard({
  payload,
  initialAnswers,
  status,
  sentAnswers,
  onAnswersChange,
  onSubmit,
  initialStep,
}: AskUserQuestionCardProps) {
  const { t } = useTranslation();
  const normalized = useMemo(() => normalizeAskUserQuestionPayload(payload), [payload]);
  const questions = normalized.questions;
  const [answers, setAnswers] = useState<Record<string, AskUserQuestionAnswer>>(() => ({
    ...initialAnswers,
  }));
  const [step, setStep] = useState(() =>
    initialStep ?? firstUnansweredStep(questions, initialAnswers),
  );
  const settled = status === 'submitted' || status === 'persisted';
  // A card closed by a typed reply has no selections to wait for, so it is finished too.
  const allAnswered =
    settled && (Boolean(normalized.repliedInChat) || !hasUnansweredQuestions(normalized, answers));
  // A finished card opens the way "Show less" leaves it. Collapse once per
  // completion, including a card that was just submitted, and leave a later
  // "Show more" open until another question is still unanswered.
  const [showAnswers, setShowAnswers] = useState(!allAnswered);
  const [autoCollapsed, setAutoCollapsed] = useState(allAnswered);
  if (allAnswered && !autoCollapsed) {
    setAutoCollapsed(true);
    setShowAnswers(false);
  } else if (!allAnswered && autoCollapsed) {
    setAutoCollapsed(false);
    if (settled) setShowAnswers(true);
  }
  const questionKey = questions.map((q) => q.uuid).join('|');
  const initialAnswersKey = JSON.stringify(initialAnswers);

  useEffect(() => {
    if (status === 'pending') return;
    setAnswers(JSON.parse(initialAnswersKey) as Record<string, AskUserQuestionAnswer>);
  }, [status, initialAnswersKey]);

  useEffect(() => {
    if (status !== 'pending') return;
    setStep((current) => {
      const currentQ = questions[current];
      if (
        currentQ &&
        !validateQuestion(
          currentQ,
          answerForQuestion(currentQ, answers, questions),
          [SOMETHING_ELSE_OPTION],
        )
      ) {
        return current;
      }
      // Every question answered (a card opened to change its answers): stay where it opened.
      const unanswered = questions.findIndex(
        (q) => !validateQuestion(q, answerForQuestion(q, answers, questions), [SOMETHING_ELSE_OPTION]),
      );
      return unanswered >= 0 ? unanswered : current;
    });
  }, [questionKey, status]);

  const syncAnswers = useCallback(
    (next: Record<string, AskUserQuestionAnswer>) => {
      setAnswers(next);
      onAnswersChange?.(next);
    },
    [onAnswersChange]
  );

  const currentQ = questions[step];
  const total = questions.length;
  const isLast = step >= total - 1;

  const stepValid = useMemo(
    () =>
      currentQ
        ? validateQuestion(
          currentQ,
          answerForQuestion(currentQ, answers, questions),
          [SOMETHING_ELSE_OPTION]
        )
        : false,
    [currentQ, answers, questions]
  );

  const setSelectionForQuestion = useCallback(
    (q: AskUserQuestionItem, selectedIds: string[], userInputs: Record<string, string>) => {
      const next = {
        ...answers,
        [q.uuid]: {
          questionUuid: q.uuid,
          selectedOptionIds: selectedIds,
          userInputs,
        },
      };
      syncAnswers(next);
    },
    [answers, syncAnswers]
  );

  const handleRadioChange = useCallback(
    (q: AskUserQuestionItem, indexStr: string) => {
      const cleanOptions = stripCatchAlls(q.options);
      const allOptions = [...cleanOptions, SOMETHING_ELSE_OPTION];
      const idx = parseInt(indexStr, 10);
      const opt = allOptions[idx];
      if (!opt) return;
      const userInputs: Record<string, string> = {};
      if (opt.isUserInput) {
        userInputs[opt.id] = answers[q.uuid]?.userInputs?.[opt.id] ?? '';
      }
      setSelectionForQuestion(q, [opt.id], userInputs);
    },
    [answers, setSelectionForQuestion]
  );

  const toggleMulti = useCallback(
    (q: AskUserQuestionItem, optionId: string, checked: boolean) => {
      const prevSel = new Set(answers[q.uuid]?.selectedOptionIds ?? []);
      if (checked) prevSel.add(optionId);
      else prevSel.delete(optionId);
      const selectedIds = [...prevSel];
      const prevInputs = { ...(answers[q.uuid]?.userInputs ?? {}) };
      const cleanOptions = stripCatchAlls(q.options);
      const allOptions = [...cleanOptions, SOMETHING_ELSE_OPTION];
      const opt = allOptions.find((o) => o.id === optionId);
      if (!checked || !opt?.isUserInput) {
        delete prevInputs[optionId];
      } else if (opt.isUserInput && prevInputs[optionId] === undefined) {
        prevInputs[optionId] = '';
      }
      setSelectionForQuestion(q, selectedIds, prevInputs);
    },
    [answers, setSelectionForQuestion]
  );

  const setUserInput = useCallback(
    (q: AskUserQuestionItem, optionId: string, text: string) => {
      const prev = answers[q.uuid] ?? {
        questionUuid: q.uuid,
        selectedOptionIds: [],
        userInputs: {},
      };
      const next = {
        ...answers,
        [q.uuid]: {
          ...prev,
          userInputs: { ...prev.userInputs, [optionId]: text },
        },
      };
      syncAnswers(next);
    },
    [answers, syncAnswers]
  );

  const handleContinue = useCallback(() => {
    if (!stepValid || !currentQ) return;
    setStep((s) => Math.min(s + 1, total - 1));
  }, [stepValid, currentQ, total]);

  const handleBack = useCallback(() => {
    setStep((s) => Math.max(0, s - 1));
  }, []);

  const handleSubmit = useCallback(() => {
    if (!stepValid || status !== 'pending') return;
    const msg = buildResumeMessage(normalized, answers, sentAnswers);
    onSubmit?.(msg, answers);
  }, [stepValid, status, normalized, answers, sentAnswers, onSubmit]);

  const handleSkip = useCallback(() => {
    if (!currentQ || status !== 'pending') return;
    const next = {
      ...answers,
      [currentQ.uuid]: {
        questionUuid: currentQ.uuid,
        selectedOptionIds: [NO_PREFERENCE_ID],
        userInputs: {},
      },
    };
    syncAnswers(next);
    if (isLast) {
      const msg = buildResumeMessage(normalized, next, sentAnswers);
      onSubmit?.(msg, next);
    } else {
      setStep((s) => Math.min(s + 1, total - 1));
    }
  }, [currentQ, status, answers, syncAnswers, isLast, normalized, sentAnswers, onSubmit, total]);

  // Every question still without an answer gets "[No preference]", and the card is sent.
  const handleSkipAll = useCallback(() => {
    if (status !== 'pending') return;
    const next = { ...answers };
    for (const q of questions) {
      if (validateQuestion(q, answerForQuestion(q, next, questions), [SOMETHING_ELSE_OPTION])) continue;
      next[q.uuid] = { questionUuid: q.uuid, selectedOptionIds: [NO_PREFERENCE_ID], userInputs: {} };
    }
    syncAnswers(next);
    onSubmit?.(buildResumeMessage(normalized, next, sentAnswers), next);
  }, [status, answers, questions, syncAnswers, onSubmit, normalized, sentAnswers]);

  const expired = status === 'pending' && isAskUserQuestionExpired(normalized);
  const closedNote = normalized.repliedInChat
    ? t('askUserQuestion.repliedInChat')
    : expired
      ? t('askUserQuestion.expired')
      : null;

  if (status === 'submitted' || status === 'persisted' || expired) {
    const heading =
      status === 'submitted'
        ? questions.length === 1
          ? t('askUserQuestion.questionsHeadingSingular')
          : t('askUserQuestion.questionsHeadingPlural')
        : questions.length === 1
          ? t('askUserQuestion.questionAskedSingular')
          : t('askUserQuestion.questionAskedPlural');
    return (
      <Card size="2">
        <Flex direction="column" gap="3" p="4">
          <Flex direction="column" gap="1">
            {normalized.userIntent ? (
              <Text size="2" color="gray">
                {normalized.userIntent}
              </Text>
            ) : null}
            <Flex align="center" justify="between" gap="3" wrap="wrap">
              <Heading size="4" style={{ margin: 0 }}>
                {heading}
              </Heading>
              <Flex
                align="center"
                gap="3"
                wrap="nowrap"
                style={{ flexShrink: 0 }}
              >
                {showAnswers && !expired && hasVisibleSelections(questions, answers) ? (
                  <Badge color="jade" size="1">
                    {t('askUserQuestion.answered')}
                  </Badge>
                ) : null}
                <Button
                  type="button"
                  variant="ghost"
                  size="1"
                  color="gray"
                  onClick={() => setShowAnswers((v) => !v)}
                >
                  <Flex align="center" gap="1">
                    {showAnswers ? t('askUserQuestion.showLess') : t('askUserQuestion.showMore')}
                    <MaterialIcon
                      name={showAnswers ? 'expand_less' : 'expand_more'}
                      size={16}
                    />
                  </Flex>
                </Button>
              </Flex>
            </Flex>
          </Flex>
          {closedNote ? (
            <Text size="2" color="gray" role="status">
              {closedNote}
            </Text>
          ) : null}
          {showAnswers ? (
            <Flex direction="column" gap="4">
              {questions.map((q, i) => {
                const rows = answeredOptions(
                  q,
                  answerForQuestion(q, answers, questions),
                  t('askUserQuestion.somethingElse'),
                  t('askUserQuestion.noPreference'),
                );
                return (
                  <Flex key={q.uuid} direction="column" gap="2">
                    <Text size="2" weight="medium">
                      {i + 1}. {q.question}
                    </Text>
                    <Flex direction="column" gap="2">
                      {rows.map((row) => (
                        <Flex key={row.id} align="start" gap="2">
                          <MaterialIcon
                            name={
                              row.selected
                                ? (q.multiSelect ? 'check_box' : 'radio_button_checked')
                                : (q.multiSelect ? 'check_box_outline_blank' : 'radio_button_unchecked')
                            }
                            size={18}
                            color={row.selected ? 'var(--jade-11)' : 'var(--slate-8)'}
                          />
                          <Text
                            size="2"
                            weight={row.selected ? 'medium' : 'regular'}
                            color={row.selected ? undefined : 'gray'}
                          >
                            {row.label}
                          </Text>
                        </Flex>
                      ))}
                    </Flex>
                  </Flex>
                );
              })}
            </Flex>
          ) : null}
        </Flex>
      </Card>
    );
  }

  if (!currentQ) return null;

  const currentAnswer = answerForQuestion(currentQ, answers, questions);
  const selectedIds = currentAnswer?.selectedOptionIds ?? [];
  const cleanOptions = stripCatchAlls(currentQ.options);
  const augmentedOptions = [...cleanOptions, SOMETHING_ELSE_OPTION];
  const selectedRadioIndex = currentQ.multiSelect
    ? -1
    : augmentedOptions.findIndex((o) => o.id === selectedIds[0]);
  const singleValue = selectedRadioIndex >= 0 ? String(selectedRadioIndex) : '';
  const somethingElseChosen = currentQ.multiSelect
    ? selectedIds.includes(SOMETHING_ELSE_ID)
    : selectedIds[0] === SOMETHING_ELSE_ID;
  const somethingElseText =
    (currentAnswer?.userInputs?.[SOMETHING_ELSE_ID] ?? '').trim();
  const isSomethingElseSelected = somethingElseChosen && somethingElseText.length > 0;
  const questionHeadingId = `ask-user-question-${currentQ.uuid}`;

  const optionText = (opt: AskUserQuestionOption) => (
    <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 0 }}>
      <Text weight="medium">
        {opt.id === SOMETHING_ELSE_ID ? t('askUserQuestion.somethingElse') : opt.label}
      </Text>
      {opt.description ? (
        <Text size="1" color="gray">
          {opt.description}
        </Text>
      ) : null}
    </Flex>
  );

  return (
    <Card
      size="2"
      variant="surface"
      style={{
        marginTop: 'var(--space-4)',
        borderRadius: 'var(--radius-4)',
        border: '1px solid var(--accent-a6)',
      }}
    >
      <Flex direction="column" gap="4" p="4">
        <Flex direction="column" gap="1">
          <Flex align="center" justify="between" gap="3" wrap="wrap">
            <Heading size="4" style={{ margin: 0 }}>
              {total === 1
                ? t('askUserQuestion.quickQuestionSingular')
                : t('askUserQuestion.quickQuestionPlural')}
            </Heading>
            <Badge size="1" variant="outline" color="gray">
              {t('askUserQuestion.stepOf', { step: step + 1, total })}
            </Badge>
          </Flex>
          {normalized.userIntent ? (
            <Text size="2" color="gray">
              {normalized.userIntent}
            </Text>
          ) : null}

        </Flex>

        <Flex direction="column" gap="1">
          {currentQ.header ? (
            <Badge size="1" variant="soft" color="gray" style={{ alignSelf: 'flex-start' }}>
              {currentQ.header}
            </Badge>
          ) : null}
          <Heading as="h3" size="3" id={questionHeadingId} style={{ margin: 0 }}>
            {step + 1}. {currentQ.question}
          </Heading>
          <Flex align="center" gap="2">
            <Text size="1" color="gray">
              {currentQ.multiSelect ? t('askUserQuestion.selectMany') : t('askUserQuestion.selectOne')}
            </Text>
            {currentQ.multiSelect && selectedIds.length > 0 ? (
              <Badge size="1" color="jade" variant="soft">
                {t('askUserQuestion.selectedCount', { count: selectedIds.length })}
              </Badge>
            ) : null}
          </Flex>
        </Flex>

        {currentQ.multiSelect ? (
          <Flex direction="column" gap="3" role="group" aria-labelledby={questionHeadingId}>
            {augmentedOptions.map((opt, idx) => {
              const checked = selectedIds.includes(opt.id);
              const isSynthetic = opt.id === SOMETHING_ELSE_ID;
              const isDisabled = false;
              return (
                <Flex key={`${opt.id}-${idx}`} direction="column" gap="2">
                  <label
                    style={{
                      cursor: isDisabled ? 'not-allowed' : 'pointer',
                      opacity: isDisabled ? 0.45 : 1,
                    }}
                  >
                    <Flex align="start" gap="3">
                      <Checkbox
                        checked={checked}
                        disabled={isDisabled}
                        onCheckedChange={(v) =>
                          toggleMulti(currentQ, opt.id, v === true)
                        }
                      />
                      {optionText(opt)}
                    </Flex>
                  </label>
                  {checked && opt.isUserInput ? (
                    <TextArea
                      placeholder={isSynthetic ? t('askUserQuestion.describeYourAnswer') : t('askUserQuestion.typeYourAnswer')}
                      value={answers[currentQ.uuid]?.userInputs?.[opt.id] ?? ''}
                      onChange={(e) =>
                        setUserInput(currentQ, opt.id, e.target.value)
                      }
                      maxLength={MAX_TYPED_ANSWER_CHARS}
                      rows={3}
                      style={{ marginLeft: '28px' }}
                    />
                  ) : null}
                </Flex>
              );
            })}
          </Flex>
        ) : (
          <RadioGroup.Root
            value={singleValue}
            onValueChange={(v) => handleRadioChange(currentQ, v)}
            aria-labelledby={questionHeadingId}
          >
            <Flex direction="column" gap="3">
              {augmentedOptions.map((opt, idx) => {
                const isSynthetic = opt.id === SOMETHING_ELSE_ID;
                const isDisabled = isSomethingElseSelected && !isSynthetic;
                const radioValue = String(idx);
                const isSelected = singleValue === radioValue;
                return (
                  <Flex
                    key={`${opt.id}-${idx}`}
                    direction="column"
                    gap="2"
                    style={{ opacity: isDisabled ? 0.45 : 1 }}
                  >
                    <label
                      style={{
                        cursor: isDisabled ? 'not-allowed' : 'pointer',
                      }}
                    >
                      <Flex align="start" gap="3">
                        <RadioGroup.Item
                          value={radioValue}
                          disabled={isDisabled}
                          style={{ marginTop: 2 }}
                        />
                        {optionText(opt)}
                      </Flex>
                    </label>
                    {isSelected && opt.isUserInput ? (
                      <TextArea
                        placeholder={isSynthetic ? t('askUserQuestion.describeYourAnswer') : t('askUserQuestion.typeYourAnswer')}
                        value={answers[currentQ.uuid]?.userInputs?.[opt.id] ?? ''}
                        onChange={(e) =>
                          setUserInput(currentQ, opt.id, e.target.value)
                        }
                        maxLength={MAX_TYPED_ANSWER_CHARS}
                        rows={3}
                        style={{ marginLeft: '28px' }}
                      />
                    ) : null}
                  </Flex>
                );
              })}
            </Flex>
          </RadioGroup.Root>
        )}

        <Flex align="center" justify="between" gap="3" wrap="wrap">
          <Button type="button" variant="soft" onClick={handleBack} disabled={step === 0}>
            {t('askUserQuestion.back')}
          </Button>
          <Flex gap="2" wrap="wrap">
            {total > 1 ? (
              <Button type="button" variant="outline" color="gray" onClick={handleSkipAll}>
                {t('askUserQuestion.skipAll')}
              </Button>
            ) : null}
            <Button
              type="button"
              variant="outline"
              color="gray"
              onClick={handleSkip}
            >
              {t('askUserQuestion.skip')}
            </Button>
            {!isLast ? (
              <Button
                type="button"
                onClick={handleContinue}
                disabled={!stepValid}
              >
                {t('askUserQuestion.next')}
              </Button>
            ) : (
              <Button
                type="button"
                onClick={handleSubmit}
                disabled={!stepValid}
              >
                {t('askUserQuestion.submit')}
              </Button>
            )}
          </Flex>
        </Flex>
      </Flex>
    </Card>
  );
}
