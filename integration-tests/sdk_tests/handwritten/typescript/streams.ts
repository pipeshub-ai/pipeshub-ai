import { expect } from "vitest";

type ChatEvent = {
  event?: string | undefined;
  data?: { [k: string]: unknown } | undefined;
};

type Message = {
  id?: string | undefined;
  messageType?: string | undefined;
};

const BOT_RESPONSE = "bot_response";

export async function readAll<T>(stream: AsyncIterable<T>): Promise<T[]> {
  const events: T[] = [];
  for await (const event of stream) {
    events.push(event);
  }
  return events;
}

/** Check that a chat stream that was read to its end produced an answer. */
export function expectFinishedRun(events: ChatEvent[]): void {
  const errors = events.filter((e) => e.event === "RUN_ERROR").map((e) => e.data);
  expect(errors, "stream reported an error").toEqual([]);
  const names = events.map((e) => e.event);
  expect(names, "no answer text in the stream").toContain("TEXT_MESSAGE_CONTENT");
  expect(names, "stream ended without RUN_FINISHED").toContain("RUN_FINISHED");
}

export function createdConversationId(events: ChatEvent[]): string {
  const created = events.find(
    (e) => e.event === "CUSTOM" && e.data?.["name"] === "conversation_created",
  );
  const value = created?.data?.["value"] as { conversationId?: string } | undefined;
  if (!value?.conversationId) {
    throw new Error("the stream did not announce a new conversation");
  }
  return value.conversationId;
}

export function lastAnswerId(messages: Message[]): string {
  const answers = messages.filter((m) => m.messageType === BOT_RESPONSE);
  const id = answers[answers.length - 1]?.id;
  if (!id) {
    throw new Error("the conversation has no answer message");
  }
  return id;
}
