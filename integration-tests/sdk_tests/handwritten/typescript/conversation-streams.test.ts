import { expect, test } from "vitest";
import { createClient } from "./client.js";
import {
  createdConversationId,
  expectFinishedRun,
  lastAnswerId,
  readAll,
} from "./streams.js";

const CHAT_MODE = "internal_search";

test("Handwritten Conversation Streams", async () => {
  const pipeshub = createClient();

  const events = await readAll(
    await pipeshub.conversations.streamChat({
      query: "Reply with the single word OK.",
      chatMode: CHAT_MODE,
    }),
  );
  const conversationId = createdConversationId(events);
  try {
    expectFinishedRun(events);

    expectFinishedRun(
      await readAll(
        await pipeshub.conversations.addMessageStream({
          conversationId,
          body: { query: "Reply with the single word YES.", chatMode: CHAT_MODE },
        }),
      ),
    );

    const detail = await pipeshub.conversations.getConversationById({
      conversationId,
    });
    const answerId = lastAnswerId(detail.conversation?.messages ?? []);

    expectFinishedRun(
      await readAll(
        await pipeshub.conversations.regenerateAnswer({
          conversationId,
          messageId: answerId,
          body: { chatMode: CHAT_MODE },
        }),
      ),
    );

    const feedback = await pipeshub.conversations.updateMessageFeedback({
      conversationId,
      messageId: answerId,
      body: { isHelpful: true },
    });
    expect(feedback.messageId).toEqual(answerId);
    expect(feedback.feedback.isHelpful).toBe(true);
  } finally {
    await pipeshub.conversations.deleteConversationById({ conversationId });
  }
});
