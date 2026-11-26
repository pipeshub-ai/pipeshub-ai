---
name: draft-reply
description: Draft a reply to a message, ready to send.
metadata:
  agent-loop:
    version: 1.0.0
    category: writing
    tags: [command, email, reply]
    status: active
    source: builtin
    pack_name: draft-reply
    pack_version: 1.0.0
    invocation: {user: true, model: false}
    arguments:
      - {name: message, description: "The message to reply to, or who it is from", required: true}
---

Draft a reply to $message. Find the message in my knowledge sources if I named it rather than pasted it. Keep the reply short, friendly and ready to send, answer every question it asks, and show only the draft.
