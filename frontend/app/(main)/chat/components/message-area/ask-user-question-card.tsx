'use client';

import React, { useCallback, useMemo, useState } from 'react';
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

export function buildAnswerMessage(
  payload: AskUserQuestionPayload,
  answers: Record<string, AskUserQuestionAnswer>
): string {
  const lines = payload.questions.map((q, i) => {
    const a = answers[q.uuid];
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
  const questions = payload.questions;
  const [step, setStep] = useState(0);
  const [showAnswers, setShowAnswers] = useState(false);
  const [answers, setAnswers] = useState<Record<string, AskUserQuestionAnswer>>(() => ({
    ...initialAnswers,
  }));

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
          answers[currentQ.uuid],
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
    (q: AskUserQuestionItem, value: string) => {
      const cleanOptions = stripCatchAlls(q.options);
      const allOptions = [...cleanOptions, SOMETHING_ELSE_OPTION];
      const opt = allOptions.find((o) => o.id === value);
      const userInputs: Record<string, string> = {};
      if (opt?.isUserInput) {
        userInputs[value] = answers[q.uuid]?.userInputs?.[value] ?? '';
      }
      setSelectionForQuestion(q, value ? [value] : [], userInputs);
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
    const msg = buildAnswerMessage(payload, answers);
    onSubmit?.(msg, answers);
  }, [stepValid, status, payload, answers, onSubmit]);

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
      const msg = buildAnswerMessage(payload, next);
      onSubmit?.(msg, next);
    } else {
      setStep((s) => Math.min(s + 1, total - 1));
    }
  }, [currentQ, status, answers, syncAnswers, isLast, payload, onSubmit, total]);

  if (status === 'submitted') {
    return (
      <Card size="2">
        <Flex direction="column" gap="3" p="4">
          <Flex direction="column" gap="1">
            {payload.userIntent ? (
              <Text size="2" color="gray">
                {payload.userIntent}
              </Text>
            ) : null}
            <Flex align="center" justify="between" gap="3" wrap="wrap">
              <Heading size="4" style={{ margin: 0 }}>
                {questions.length === 1
                  ? t('askUserQuestion.questionsHeadingSingular')
                  : t('askUserQuestion.questionsHeadingPlural')}
              </Heading>
              <Flex
                align="center"
                gap="3"
                wrap="nowrap"
                style={{ flexShrink: 0 }}
              >
                {showAnswers ? (
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
                const a = answers[q.uuid];
                const labels = (a?.selectedOptionIds ?? []).map((id) => {
                  if (id === NO_PREFERENCE_ID) return t('askUserQuestion.noPreference');
                  if (id === SOMETHING_ELSE_ID) {
                    return a?.userInputs?.[id]?.trim() || t('askUserQuestion.somethingElse');
                  }
                  const opt = q.options.find((o) => o.id === id);
                  const ut = a?.userInputs?.[id]?.trim();
                  if (opt?.isUserInput && ut) return `${opt.label}: ${ut}`;
                  return opt?.label ?? id;
                });
                return (
                  <Flex key={q.uuid} direction="column" gap="2">
                    <Text size="2">
                      {i + 1}. {q.question}
                    </Text>
                    <Flex gap="2" wrap="wrap">
                      {labels.map((label, i) => (
                        <Badge key={`${q.uuid}-${i}`} size="1" variant="soft">
                          {label}
                        </Badge>
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

  if (status === 'persisted') {
    return (
      <Card size="2">
        <Flex direction="column" gap="3" p="4">
          <Flex direction="column" gap="1">
            {payload.userIntent ? (
              <Text size="2" color="gray">
                {payload.userIntent}
              </Text>
            ) : null}
            <Flex align="center" justify="between" gap="3" wrap="wrap">
              <Heading size="4" style={{ margin: 0 }}>
                {payload.questions.length === 1
                  ? t('askUserQuestion.questionAskedSingular')
                  : t('askUserQuestion.questionAskedPlural')}
              </Heading>
              <Flex
                align="center"
                gap="3"
                wrap="nowrap"
                style={{ flexShrink: 0 }}
              >
                {showAnswers ? (
                  <Badge color="gray" size="1" variant="outline">
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
              {questions.map((q, i) => (
                <Flex key={q.uuid} direction="column" gap="2">
                  <Text size="2" weight="medium">
                    {i + 1}. {q.question}
                  </Text>
                  <Flex direction="column" gap="1" style={{ paddingLeft: 'var(--space-3)' }}>
                    {q.options.map((opt) => (
                      <Flex key={opt.id} align="start" gap="2">
                        <Text size="1" color="gray" style={{ lineHeight: '1.5' }}>•</Text>
                        <Text size="2">{opt.label}</Text>
                      </Flex>
                    ))}
                  </Flex>
                </Flex>
              ))}
            </Flex>
          ) : null}
        </Flex>
      </Card>
    );
  }

  if (!currentQ) return null;

  const selectedIds = answers[currentQ.uuid]?.selectedOptionIds ?? [];
  const singleValue = currentQ.multiSelect ? '' : selectedIds[0] ?? '';
  const cleanOptions = stripCatchAlls(currentQ.options);
  const augmentedOptions = [...cleanOptions, SOMETHING_ELSE_OPTION];
  const somethingElseChosen = currentQ.multiSelect
    ? selectedIds.includes(SOMETHING_ELSE_ID)
    : singleValue === SOMETHING_ELSE_ID;
  const somethingElseText =
    (answers[currentQ.uuid]?.userInputs?.[SOMETHING_ELSE_ID] ?? '').trim();
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
            {payload.userIntent ? (
              <Text size="2" color="gray" style={{ marginTop: 'var(--space-1)' }}>
                {payload.userIntent}
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
            {augmentedOptions.map((opt) => {
              const checked = selectedIds.includes(opt.id);
              const isSynthetic = opt.id === SOMETHING_ELSE_ID;
              const isDisabled = false;
              return (
                <Flex key={opt.id} direction="column" gap="2">
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
              {augmentedOptions.map((opt) => {
                const isSynthetic = opt.id === SOMETHING_ELSE_ID;
                const isDisabled = isSomethingElseSelected && !isSynthetic;
                return (
                  <Flex
                    key={opt.id}
                    direction="column"
                    gap="2"
                    style={{ opacity: isDisabled ? 0.45 : 1 }}
                  >
                    <Flex align="start" gap="3" style={{ cursor: isDisabled ? 'not-allowed' : 'pointer' }}>
                      <RadioGroup.Item
                        value={opt.id}
                        disabled={isDisabled}
                        style={{ marginTop: 2 }}
                      />
                      <Flex direction="column" gap="1" style={{ flex: 1 }}>
                        <Text weight="medium">
                          {opt.id === SOMETHING_ELSE_ID ? t('askUserQuestion.somethingElse') : opt.label}
                        </Text>
                      </Flex>
                    </Flex>
                    {singleValue === opt.id && opt.isUserInput ? (
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
