export interface AskUserQuestionOption {
  id: string;
  label: string;
  isUserInput?: boolean;
}

export interface AskUserQuestionItem {
  uuid: string;
  question: string;
  options: AskUserQuestionOption[];
  multiSelect?: boolean;
}

export interface AskUserQuestionPayload {
  name?: string;
  userIntent?: string;
  questions: AskUserQuestionItem[];
}

export interface AskUserQuestionEvent {
  status?: string;
  toolData?: AskUserQuestionPayload;
}

export function isAskUserQuestionToolName(toolName: string): boolean {
  const name = toolName.trim().toLowerCase();
  return (
    name === "ask_user_question" ||
    name.endsWith("__ask_user_question") ||
    name.endsWith("_ask_user_question") ||
    name.endsWith(".ask_user_question")
  );
}

function parseJsonObject(raw: unknown): Record<string, unknown> | null {
  let value = raw;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return null;
    }
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function normalizeQuestions(raw: unknown): AskUserQuestionItem[] {
  if (!Array.isArray(raw)) return [];
  const questions: AskUserQuestionItem[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const q = item as Record<string, unknown>;
    const question =
      typeof q.question === "string"
        ? q.question
        : typeof q.prompt === "string"
          ? q.prompt
          : "";
    if (!question.trim()) continue;
    const uuid =
      (typeof q.uuid === "string" && q.uuid) ||
      (typeof q.id === "string" && q.id) ||
      `q-${questions.length + 1}`;
    questions.push({
      uuid,
      question: question.trim(),
      options: Array.isArray(q.options)
        ? (q.options as AskUserQuestionOption[])
        : [],
      multiSelect: Boolean(q.multiSelect),
    });
  }
  return questions;
}

/**
 * Accepts CUSTOM `{status,toolData}`, raw tool result, or tool args and
 * returns a normalized ask-user event (or null when no questions exist).
 */
export function coerceAskUserQuestionEvent(
  raw: unknown,
): AskUserQuestionEvent | null {
  const obj = parseJsonObject(raw);
  if (!obj) return null;

  const toolDataRaw =
    obj.toolData && typeof obj.toolData === "object"
      ? (obj.toolData as Record<string, unknown>)
      : obj;

  const questions = normalizeQuestions(toolDataRaw.questions);
  if (questions.length === 0) return null;

  const userIntent =
    (typeof toolDataRaw.userIntent === "string" && toolDataRaw.userIntent) ||
    (typeof toolDataRaw.user_intent === "string" && toolDataRaw.user_intent) ||
    undefined;

  return {
    status: typeof obj.status === "string" ? obj.status : "success",
    toolData: {
      name:
        typeof toolDataRaw.name === "string"
          ? toolDataRaw.name
          : "ask_user_question",
      ...(userIntent ? { userIntent } : {}),
      questions,
    },
  };
}

/** Formats ask_user_question tool data as plain Slack mrkdwn (questions only). */
export function formatAskUserQuestionMrkdwn(
  event: AskUserQuestionEvent,
): string | null {
  const normalized = coerceAskUserQuestionEvent(event);
  const questions = normalized?.toolData?.questions;
  if (!questions || questions.length === 0) {
    return null;
  }

  const lines: string[] = ["*I need a bit more information:*"];

  const intent =
    typeof normalized.toolData?.userIntent === "string"
      ? normalized.toolData.userIntent.trim()
      : "";
  if (intent) {
    lines.push(`_${intent}_`);
  }

  questions.forEach((q, index) => {
    lines.push("");
    lines.push(`${index + 1}. ${q.question}`);
  });

  lines.push("");
  lines.push("_Reply in this thread with your answers._");
  return lines.join("\n");
}
