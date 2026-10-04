import { expect, test } from "vitest";
import { createClient } from "./client.js";
import { readAll } from "./streams.js";

const FILE_NAME = "sdk-test-record.txt";
const FILE_CONTENT = "PipesHub SDK test file.\n";

test("Handwritten Record Lifecycle", async () => {
  const pipeshub = createClient();

  const kb = await pipeshub.knowledgeBase.createKnowledgeBase({
    kbName: "sdk-test-records",
  });
  const kbId = kb.id;
  try {
    const events = await readAll(
      await pipeshub.knowledgeBase.uploadRecords({
        kbId,
        body: {
          files: [
            { fileName: FILE_NAME, content: new TextEncoder().encode(FILE_CONTENT) },
          ],
        },
      }),
    );
    const succeeded = events.filter((e) => e.event === "file:succeeded");
    expect(succeeded, "upload did not succeed").toHaveLength(1);
    const recordId = String(succeeded[0]?.data?.["recordId"]);
    const done = events.find((e) => e.event === "done");
    expect(done?.data?.["summary"]).toEqual({ total: 1, succeeded: 1, failed: 0 });

    const buffer = await pipeshub.knowledgeBase.streamRecordBuffer({ recordId });
    expect(await new Response(buffer).text()).toEqual(FILE_CONTENT);

    const updated = await pipeshub.knowledgeBase.updateRecord({
      recordId,
      body: { recordName: "sdk-test-record-renamed" },
    });
    expect(updated.record?.id).toEqual(recordId);

    const folder = await pipeshub.knowledgeBase.createFolder({
      kbId,
      body: { folderName: "sdk-test-destination" },
    });
    const moved = await pipeshub.knowledgeBase.moveRecord({
      kbId,
      recordId,
      body: { newParentId: folder.id },
    });
    expect(moved.success).toBe(true);

    const reindexed = await pipeshub.knowledgeBase.reindexRecord({ recordId });
    expect(reindexed.success).toBe(true);

    const deleted = await pipeshub.knowledgeBase.deleteRecord({ recordId });
    expect(deleted.success).toBe(true);
  } finally {
    await pipeshub.knowledgeBase.deleteKnowledgeBase({ kbId });
  }
});
