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
} from '../../types';

const SOMETHING_ELSE_ID = '__something_else__';
const NO_PREFERENCE_ID = '__no_preference__';

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
      return {
        id: opt.id || stableId('opt', label, oi),
        label,
        isUserInput: Boolean(opt.isUserInput),
      };
    });
    return {
      uuid: q.uuid || stableId('q', question, qi),
      question,
      options,
      multiSelect: Boolean(q.multiSelect),
    };
  });
  return {
    name: 'ask_user_question',
    ...(raw.userIntent ? { userIntent: raw.userIntent } : {}),
    questions,
  };
}

export function mergeAskUserQuestionPayloads(
  existing: AskUserQuestionPayload,
  incoming: AskUserQuestionPayload,
): AskUserQuestionPayload {
  const a = normalizeAskUserQuestionPayload(existing);
  const b = normalizeAskUserQuestionPayload(incoming);
  const seen = new Set(a.questions.map((q) => q.question.trim().toLowerCase()));
  const extra = b.questions.filter((q) => !seen.has(q.question.trim().toLowerCase()));
  return {
    name: 'ask_user_question',
    userIntent: a.userIntent || b.userIntent,
    questions: [...a.questions, ...extra],
  };
}

export function buildAnswerMessage(
  payload: AskUserQuestionPayload,
  answers: Record<string, AskUserQuestionAnswer>
): string {
  const questions = normalizeAskUserQuestionPayload(payload).questions;
  const lines = questions.map((q, i) => {
    const a = answerForQuestion(q, answers);
    const parts = (a?.selectedOptionIds ?? [])
      .map((optId) => {
        if (optId === NO_PREFERENCE_ID) return '[No preference]';
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
    for (const label of rawParts.split(', ').map((s) => s.trim()).filter(Boolean)) {
      if (label === '[No preference]') {
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
): AskUserQuestionAnswer | undefined {
  if (q.uuid && answers[q.uuid]) return answers[q.uuid];
  const values = Object.values(answers);
  const byUuid = values.find((a) => a.questionUuid === q.uuid);
  if (byUuid) return byUuid;
  const labels = new Set(
    (q.options ?? []).map((o) => optionLabel(o).trim().toLowerCase()).filter(Boolean),
  );
  const ids = new Set((q.options ?? []).map((o) => optionId(o)));
  return values.find((a) =>
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
    !validateQuestion(q, answerForQuestion(q, answers), [SOMETHING_ELSE_OPTION]),
  );
  return idx >= 0 ? idx : Math.max(0, questions.length - 1);
}

function hasVisibleSelections(
  questions: AskUserQuestionItem[],
  answers: Record<string, AskUserQuestionAnswer>,
): boolean {
  return questions.some((q) => {
    const a = answerForQuestion(q, answers);
    return (a?.selectedOptionIds?.length ?? 0) > 0;
  });
}

export interface AskUserQuestionCardProps {
  payload: AskUserQuestionPayload;
  initialAnswers: Record<string, AskUserQuestionAnswer>;
  status: 'pending' | 'submitted' | 'persisted';
  onAnswersChange?: (answers: Record<string, AskUserQuestionAnswer>) => void;
  onSubmit?: (message: string, answers: Record<string, AskUserQuestionAnswer>) => void;
}

export function AskUserQuestionCard({
  payload,
  initialAnswers,
  status,
  onAnswersChange,
  onSubmit,
}: AskUserQuestionCardProps) {
  const { t } = useTranslation();
  const normalized = useMemo(() => normalizeAskUserQuestionPayload(payload), [payload]);
  const questions = normalized.questions;
  const [answers, setAnswers] = useState<Record<string, AskUserQuestionAnswer>>(() => ({
    ...initialAnswers,
  }));
  const [step, setStep] = useState(() =>
    firstUnansweredStep(questions, initialAnswers),
  );
  const [showAnswers, setShowAnswers] = useState(true);
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
          answerForQuestion(currentQ, answers),
          [SOMETHING_ELSE_OPTION],
        )
      ) {
        return current;
      }
      return firstUnansweredStep(questions, answers);
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
          answerForQuestion(currentQ, answers),
          [SOMETHING_ELSE_OPTION]
        )
        : false,
    [currentQ, answers]
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
    const msg = buildAnswerMessage(normalized, answers);
    onSubmit?.(msg, answers);
  }, [stepValid, status, normalized, answers, onSubmit]);

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
      const msg = buildAnswerMessage(normalized, next);
      onSubmit?.(msg, next);
    } else {
      setStep((s) => Math.min(s + 1, total - 1));
    }
  }, [currentQ, status, answers, syncAnswers, isLast, normalized, onSubmit, total]);

  if (status === 'submitted' || status === 'persisted') {
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
                {showAnswers && hasVisibleSelections(questions, answers) ? (
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
          {showAnswers ? (
            <Flex direction="column" gap="4">
              {questions.map((q, i) => {
                const rows = answeredOptions(
                  q,
                  answerForQuestion(q, answers),
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

  const currentAnswer = answerForQuestion(currentQ, answers);
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
            {normalized.userIntent ? (
              <Text size="2" color="gray" style={{ marginTop: 'var(--space-1)' }}>
                {normalized.userIntent}
              </Text>
            ) : null}
            <Heading size="4" style={{ margin: 0 }}>
              {total === 1
                ? t('askUserQuestion.quickQuestionSingular')
                : t('askUserQuestion.quickQuestionPlural')}
            </Heading>
            <Badge size="1" variant="outline" color="gray">
              {t('askUserQuestion.stepOf', { step: step + 1, total })}
            </Badge>
          </Flex>

        </Flex>

        <Flex direction="column" gap="1">
          <Heading as="h3" size="3" style={{ margin: 0 }}>
            {step + 1}. {currentQ.question}
          </Heading>
          <Flex align="center" gap="2">
            {/* <Text size="1" color="gray">
              {currentQ.multiSelect ? 'Select all that apply' : 'Select one'}
            </Text> */}
            {currentQ.multiSelect && selectedIds.length > 0 ? (
              <Badge size="1" color="jade" variant="soft">
                {t('askUserQuestion.selectedCount', { count: selectedIds.length })}
              </Badge>
            ) : null}
          </Flex>
        </Flex>

        {currentQ.multiSelect ? (
          <div style={{ maxHeight: '160px', overflowY: 'auto', paddingRight: 'var(--space-2)' }}>
          <Flex direction="column" gap="3">
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
                      <Flex direction="column" gap="1" style={{ flex: 1 }}>
                        <Text weight="medium">
                          {opt.id === SOMETHING_ELSE_ID ? t('askUserQuestion.somethingElse') : opt.label}
                        </Text>
                      </Flex>
                    </Flex>
                  </label>
                  {checked && opt.isUserInput ? (
                    <TextArea
                      placeholder={isSynthetic ? t('askUserQuestion.describeYourAnswer') : t('askUserQuestion.typeYourAnswer')}
                      value={answers[currentQ.uuid]?.userInputs?.[opt.id] ?? ''}
                      onChange={(e) =>
                        setUserInput(currentQ, opt.id, e.target.value)
                      }
                      rows={3}
                      style={{ marginLeft: '28px' }}
                    />
                  ) : null}
                </Flex>
              );
            })}
          </Flex>
          </div>
        ) : (
          <RadioGroup.Root
            value={singleValue}
            onValueChange={(v) => handleRadioChange(currentQ, v)}
          >
            <div style={{ maxHeight: '160px', overflowY: 'auto', paddingRight: 'var(--space-2)' }}>
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
                        <Flex direction="column" gap="1" style={{ flex: 1 }}>
                          <Text weight="medium">
                            {opt.id === SOMETHING_ELSE_ID ? t('askUserQuestion.somethingElse') : opt.label}
                          </Text>
                        </Flex>
                      </Flex>
                    </label>
                    {isSelected && opt.isUserInput ? (
                      <TextArea
                        placeholder={isSynthetic ? t('askUserQuestion.describeYourAnswer') : t('askUserQuestion.typeYourAnswer')}
                        value={answers[currentQ.uuid]?.userInputs?.[opt.id] ?? ''}
                        onChange={(e) =>
                          setUserInput(currentQ, opt.id, e.target.value)
                        }
                        rows={3}
                        style={{ marginLeft: '28px' }}
                      />
                    ) : null}
                  </Flex>
                );
              })}
            </Flex>
            </div>
          </RadioGroup.Root>
        )}

        <Flex align="center" justify="between" gap="3" wrap="wrap">
          <Button type="button" variant="soft" onClick={handleBack} disabled={step === 0}>
            {t('askUserQuestion.back')}
          </Button>
          <Flex gap="2">
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
