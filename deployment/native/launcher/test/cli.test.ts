import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { emitter, parse } from "../src/cli.ts";

describe("parse", () => {
  test("command, options, json", () => {
    const p = parse(["start", "--data-dir", "/x y", "--json", "--detach", "--port-base", "54800"]);
    assert.equal(p.cmd, "start");
    assert.equal(p.json, true);
    assert.equal(p.detach, true);
    assert.deepEqual(p.opts, { dataDir: "/x y", portBase: 54800 });
    assert.deepEqual(p.rest, ["--data-dir", "/x y", "--port-base", "54800"]);   // what --detach hands to the background launcher
    assert.equal(parse(["status", "--data-dir=/d"]).opts.dataDir, "/d");
  });
  test("unknown, incomplete or out-of-range options are errors", () => {
    assert.throws(() => parse(["start", "--nope"]), /unknown option/);
    assert.throws(() => parse(["start", "--data-dir"]), /needs a value/);
    assert.throws(() => parse(["start", "--port-base", "abc"]), /between 1024 and 65000/);
    // an empty folder would be the current one; an option is not a folder; a second command is not silently dropped
    assert.throws(() => parse(["stop", "--data-dir", ""]), /needs a value/);
    assert.throws(() => parse(["stop", "--data-dir="]), /needs a value/);
    assert.throws(() => parse(["stop", "--data-dir", "--json"]), /needs a value/);
    assert.throws(() => parse(["stop", "now"]), /unexpected argument/);
    assert.equal(parse(["stop", "--data-dir", "./--x"]).opts.dataDir, "./--x");
  });
});

describe("output", () => {
  test("json: one object per line; text: readable", () => {
    const lines: string[] = [];
    emitter(true, (s) => lines.push(s))({ event: "ready", url: "http://127.0.0.1:1" });
    assert.deepEqual(JSON.parse(lines[0]!), { event: "ready", url: "http://127.0.0.1:1" });
    emitter(false, (s) => lines.push(s))({ event: "progress", stage: "postgres", message: "Starting the database" });
    assert.equal(lines[1], "... Starting the database");
    emitter(false, (s) => lines.push(s))({ event: "ready", url: "http://127.0.0.1:1", healthy_after_s: 2 });
    assert.equal(lines[2], "ready: http://127.0.0.1:1 (2 s)");
  });
});
