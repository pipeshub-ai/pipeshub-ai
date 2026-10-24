import { expect, test } from "vitest";
import { createClient } from "./client.js";
import { createdConversationId, expectFinishedRun, readAll } from "./streams.js";

test("Handwritten Conversation Lifecycle", async () => {
  const pipeshub = createClient();

  const events = await readAll(
    await pipeshub.conversations.streamChat({
      query: "Reply with the single word OK.",
      chatMode: "internal_search",
    }),
  );
  const conversationId = createdConversationId(events);
  try {
    expectFinishedRun(events);

    const titled = await pipeshub.conversations.updateConversationTitle({
      conversationId,
      body: { title: "sdk-test-title" },
    });
    expect(titled.conversation.title).toEqual("sdk-test-title");

    const archived = await pipeshub.conversations.archiveConversation({ conversationId });
    expect(archived.status).toEqual("archived");

    const unarchived = await pipeshub.conversations.unarchiveConversation({
      conversationId,
    });
    expect(unarchived.status).toEqual("unarchived");
  } finally {
    await pipeshub.conversations.deleteConversationById({ conversationId });
  }
});
