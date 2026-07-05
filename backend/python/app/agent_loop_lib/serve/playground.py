"""Minimal static playground UI (Phase 4) — a single dependency-free HTML
page served at GET /playground. Talks to POST /runs itself via `fetch` +
manual SSE-frame parsing (not the browser's built-in `EventSource`, which
can only issue GET requests and can't carry a JSON body) so a human can
poke the agent loop from a browser without any build step or extra
frontend tooling.
"""

PLAYGROUND_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>agent-loop playground</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    background: #0d1117; color: #c9d1d9; height: 100vh; display: flex; flex-direction: column;
  }
  header {
    padding: 12px 16px; border-bottom: 1px solid #30363d; display: flex; gap: 10px; align-items: center;
    flex-wrap: wrap;
  }
  header h1 { font-size: 15px; margin: 0; color: #58a6ff; font-weight: 600; }
  header input, header select, header button {
    background: #161b22; color: #c9d1d9; border: 1px solid #30363d; border-radius: 6px;
    padding: 6px 10px; font-family: inherit; font-size: 13px;
  }
  header button {
    background: #238636; border-color: #2ea043; color: white; cursor: pointer; font-weight: 600;
  }
  header button:disabled { background: #21262d; border-color: #30363d; color: #6e7681; cursor: not-allowed; }
  header button.stop { background: #da3633; border-color: #f85149; }
  #goal { flex: 1; min-width: 220px; }
  main { flex: 1; display: flex; overflow: hidden; }
  #output {
    flex: 1.4; overflow-y: auto; padding: 16px; white-space: pre-wrap; word-wrap: break-word;
    line-height: 1.5; font-size: 14px; border-right: 1px solid #30363d;
  }
  #events { flex: 1; overflow-y: auto; padding: 12px; font-size: 12px; }
  .event {
    padding: 4px 8px; margin-bottom: 3px; border-radius: 4px; background: #161b22;
    border-left: 3px solid #30363d; color: #8b949e;
  }
  .event .type { color: #58a6ff; font-weight: 600; }
  .event.tool_call, .event.tool_call_start { border-left-color: #d29922; }
  .event.tool_result, .event.tool_call_end { border-left-color: #3fb950; }
  .event.error, .event.run_error { border-left-color: #f85149; color: #ffa198; }
  .event.done, .event.run_finished, .event.agent_complete { border-left-color: #3fb950; }
  .section-title { color: #6e7681; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; padding: 8px 12px 4px; }
  .cursor { display: inline-block; width: 7px; height: 14px; background: #58a6ff; vertical-align: text-bottom; animation: blink 1s step-start infinite; }
  @keyframes blink { 50% { opacity: 0; } }
</style>
</head>
<body>
<header>
  <h1>agent-loop</h1>
  <input id="goal" type="text" placeholder="Ask the agent something..." autofocus>
  <select id="role">
    <option value="assistant">assistant</option>
    <option value="researcher">researcher</option>
    <option value="writer">writer</option>
    <option value="coder">coder</option>
  </select>
  <button id="run">Run</button>
</header>
<main>
  <div id="output"></div>
  <div id="events"><div class="section-title">event stream</div></div>
</main>
<script>
const $ = (id) => document.getElementById(id);
const outputEl = $("output");
const eventsEl = $("events");
const runBtn = $("run");
const goalEl = $("goal");
const roleEl = $("role");

let currentController = null;

function logEvent(type, payload) {
  const div = document.createElement("div");
  div.className = "event " + type;
  const short = JSON.stringify(payload || {});
  div.innerHTML = `<span class="type">${type}</span> ${short.length > 200 ? short.slice(0, 200) + "…" : short}`;
  eventsEl.appendChild(div);
  eventsEl.scrollTop = eventsEl.scrollHeight;
}

function setRunning(running) {
  runBtn.textContent = running ? "Stop" : "Run";
  runBtn.classList.toggle("stop", running);
  goalEl.disabled = running;
}

async function runGoal() {
  const goal = goalEl.value.trim();
  if (!goal) return;

  outputEl.textContent = "";
  eventsEl.innerHTML = '<div class="section-title">event stream</div>';
  const cursor = document.createElement("span");
  cursor.className = "cursor";
  outputEl.appendChild(cursor);

  currentController = new AbortController();
  setRunning(true);

  try {
    const resp = await fetch("/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ goal, role: roleEl.value }),
      signal: currentController.signal,
    });
    if (!resp.ok || !resp.body) {
      const text = await resp.text();
      logEvent("error", { error: text || resp.statusText });
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sep;
      while ((sep = buffer.indexOf("\\n\\n")) !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const lines = frame.split("\\n");
        let eventType = "message";
        let data = "{}";
        for (const line of lines) {
          if (line.startsWith("event:")) eventType = line.slice(6).trim();
          if (line.startsWith("data:")) data = line.slice(5).trim();
        }
        let payload = {};
        try { payload = JSON.parse(data); } catch (e) { /* ignore */ }

        logEvent(eventType, payload);

        if (eventType === "text_message_content" && payload.delta) {
          cursor.insertAdjacentText("beforebegin", payload.delta);
        } else if (eventType === "done") {
          cursor.remove();
          if (payload.output) {
            const out = document.createElement("div");
            out.style.marginTop = "12px";
            out.style.color = "#3fb950";
            out.textContent = "\\n[result] " + payload.output;
            outputEl.appendChild(out);
          }
        } else if (eventType === "error" || eventType === "run_error") {
          cursor.remove();
          const err = document.createElement("div");
          err.style.color = "#f85149";
          err.textContent = "\\n[error] " + (payload.error || JSON.stringify(payload));
          outputEl.appendChild(err);
        }
      }
    }
  } catch (err) {
    if (err.name !== "AbortError") logEvent("error", { error: String(err) });
  } finally {
    setRunning(false);
    currentController = null;
  }
}

runBtn.addEventListener("click", () => {
  if (currentController) {
    currentController.abort();
    setRunning(false);
    return;
  }
  runGoal();
});
goalEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !currentController) runGoal();
});
</script>
</body>
</html>
"""
