import { expect, test } from "vitest";
import { createClient } from "./client.js";
import { expectStatus } from "./errors.js";
import {
  createdConversationId,
  expectFinishedRun,
  lastAnswerId,
  readAll,
} from "./streams.js";

const CHAT_MODE = "quick";

test("Handwritten Agent Conversation Streams", async () => {
  const pipeshub = createClient();

  const created = await pipeshub.agents.createAgent({
    name: "sdk-test-agent-streams",
  });
  const agentKey = created.agent.key;
  try {
    const events = await readAll(
      await pipeshub.agents.streamAgentConversation({
        agentKey,
        body: { query: "Reply with the single word OK.", chatMode: CHAT_MODE },
      }),
    );
    expectFinishedRun(events);
    const conversationId = createdConversationId(events);

    expectFinishedRun(
      await readAll(
        await pipeshub.agents.streamAgentConversationMessage({
          agentKey,
          conversationId,
          body: { query: "Reply with the single word YES.", chatMode: CHAT_MODE },
        }),
      ),
    );

    const detail = await pipeshub.agents.getAgentConversationById({
      agentKey,
      conversationId,
    });
    const answerId = lastAnswerId(detail.conversation.messages);

    expectFinishedRun(
      await readAll(
        await pipeshub.agents.regenerateAgentConversationMessage({
          agentKey,
          conversationId,
          messageId: answerId,
          body: { chatMode: CHAT_MODE },
        }),
      ),
    );

    const feedback = await pipeshub.agents.updateAgentConversationMessageFeedback({
      agentKey,
      conversationId,
      messageId: answerId,
      body: { isHelpful: true },
    });
    expect(feedback.messageId).toEqual(answerId);
    expect(feedback.feedback.isHelpful).toBe(true);

    const fork = await pipeshub.agents.forkAgentConversation({
      agentKey,
      conversationId,
      messageId: answerId,
    });
    expect(fork.forkedFrom).toEqual({ conversationId, messageId: answerId });

    const upload = await pipeshub.agents.uploadAgentConversationChatAttachments({
      agentKey,
      body: {
        conversationId,
        files: [
          {
            fileName: "sdk-test-attachment.txt",
            content: new TextEncoder().encode("PipesHub SDK test attachment.\n"),
          },
        ],
      },
    });
    expect(upload.conversationId).toEqual(conversationId);
    const recordId = upload.attachments[0]?.recordId;
    expect(recordId, "upload returned no attachment").toBeDefined();
    await pipeshub.agents.deleteAgentConversationChatAttachment({
      agentKey,
      recordId: recordId!,
    });
    await expectStatus(
      pipeshub.agents.deleteAgentConversationChatAttachment({ agentKey, recordId: recordId! }),
      404,
    );
  } finally {
    // Deleting the agent removes its conversations with it.
    await pipeshub.agents.deleteAgent({ agentKey });
  }
});
