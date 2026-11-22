#!/usr/bin/env node
// Print the value expressions of TypeScript log calls, as JSON lines, for scripts/log_pii.py to classify.
// One classifier, in log_pii.py, serves both runtimes; this file only parses.
//
// A log call is <x>.<level>(...) where a word of <x> is log, logger, logging or console (this.logger, this._log, auditLogger).
// Each argument is reduced to leaves, the same way log_pii.py does for Python: identifiers, property and element
// access, .get('k'), template spans, + / || / ?? operands, object literal keys and values, array elements,
// String()/JSON.stringify()/toLowerCase()/join() pass-throughs, and what a function passed to .map() returns.
//
// Usage: TS_MODULE=<path to the typescript package> node log_pii_ts.cjs <file>...
// Output: {"path","line","col","leaf_line","level","terminal","owner","expr","ok","is_key"} per leaf (is_key: an object key). path is the argument as
// given; line and col are the call's start; ok means a `// log-pii: ok <reason>` comment sits on the leaf's own line(s).
const fs = require('fs');
const ts = require(process.env.TS_MODULE || 'typescript');

const LEVELS = new Set(['debug', 'info', 'warn', 'error', 'verbose', 'silly', 'fatal', 'trace', 'log']);
const PASS_METHODS = new Set(['toLowerCase', 'toUpperCase', 'trim', 'toString', 'join', 'concat', 'replace',
  'split', 'slice', 'substring']);
const LOGGER_WORDS = new Set(['log', 'logger', 'logging', 'console']);
const PASS_FUNCS = new Set(['String', 'JSON.stringify']);
const OK = /\/\/\s*log-pii:\s*ok\s+\S/;

function nameOf(node) {
  if (ts.isIdentifier(node) || ts.isPrivateIdentifier(node)) return node.text;
  if (ts.isStringLiteralLike(node) || ts.isNumericLiteral(node)) return node.text;
  return null;
}

// [terminal, owner] for an access expression, or null.
function terminal(node) {
  node = unwrap(node);
  if (ts.isIdentifier(node)) return [node.text, null];
  if (node.kind === ts.SyntaxKind.ThisKeyword) return ['this', null];
  if (ts.isPropertyAccessExpression(node)) return [node.name.text, owner(node.expression)];
  if (ts.isElementAccessExpression(node) && ts.isStringLiteralLike(node.argumentExpression)) {
    return [node.argumentExpression.text, owner(node.expression)];
  }
  if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression) &&
      node.expression.name.text === 'get' && node.arguments[0] && ts.isStringLiteralLike(node.arguments[0])) {
    return [node.arguments[0].text, owner(node.expression.expression)];
  }
  return null;
}

function owner(node) {
  const t = terminal(node);
  return t ? t[0] : null;
}

function unwrap(node) {
  while (ts.isParenthesizedExpression(node) || ts.isAsExpression(node) || ts.isNonNullExpression(node) ||
         (ts.isSatisfiesExpression && ts.isSatisfiesExpression(node)) || ts.isTypeAssertionExpression(node)) {
    node = node.expression;
  }
  return node;
}

function* leaves(node) {
  node = unwrap(node);
  if (ts.isIdentifier(node) || ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) {
    yield node;
  } else if (ts.isCallExpression(node)) {
    const callee = node.expression;
    const calleeText = callee.getText();
    if (ts.isPropertyAccessExpression(callee) && callee.name.text === 'get') {
      yield node;
    } else if (PASS_FUNCS.has(calleeText)) {
      for (const a of node.arguments) yield* leaves(a);
    } else if (ts.isPropertyAccessExpression(callee) && PASS_METHODS.has(callee.name.text)) {
      yield* leaves(callee.expression);
      if (callee.name.text === 'join' || callee.name.text === 'concat') for (const a of node.arguments) yield* leaves(a);
    } else if (ts.isPropertyAccessExpression(callee) && callee.name.text === 'map' && node.arguments[0] &&
               (ts.isArrowFunction(node.arguments[0]) || ts.isFunctionExpression(node.arguments[0]))) {
      const body = node.arguments[0].body;
      if (ts.isBlock(body)) for (const r of returns(body)) yield* leaves(r);
      else yield* leaves(body);
    }
  } else if (ts.isTemplateExpression(node)) {
    for (const span of node.templateSpans) yield* leaves(span.expression);
  } else if (ts.isBinaryExpression(node)) {
    const op = node.operatorToken.kind;
    if ([ts.SyntaxKind.PlusToken, ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken,
         ts.SyntaxKind.AmpersandAmpersandToken].includes(op)) {
      yield* leaves(node.left);
      yield* leaves(node.right);
    }
  } else if (ts.isConditionalExpression(node)) {
    yield* leaves(node.whenTrue);
    yield* leaves(node.whenFalse);
  } else if (ts.isObjectLiteralExpression(node)) {
    for (const p of node.properties) {
      if (ts.isPropertyAssignment(p)) {
        const key = nameOf(p.name);
        if (key) yield { key, node: p.name };
        yield* leaves(p.initializer);
      } else if (ts.isShorthandPropertyAssignment(p)) {
        yield p.name;
      } else if (ts.isSpreadAssignment(p)) {
        yield* leaves(p.expression);
      }
    }
  } else if (ts.isArrayLiteralExpression(node)) {
    for (const e of node.elements) yield* leaves(ts.isSpreadElement(e) ? e.expression : e);
  } else if (ts.isSpreadElement(node)) {
    yield* leaves(node.expression);
  }
}

// The values a block body returns: its return statements, not those of functions nested in it.
function* returns(node) {
  for (const child of node.getChildren()) {
    if (ts.isReturnStatement(child) && child.expression) yield child.expression;
    else if (!ts.isFunctionLike(child) && !ts.isClassLike(child)) yield* returns(child);
  }
}

function logLevel(call) {
  const callee = call.expression;
  if (!ts.isPropertyAccessExpression(callee) || !LEVELS.has(callee.name.text)) return null;
  // Words of the receiver, so this.logger, this._log and auditLogger count and catalogService does not.
  const words = callee.expression.getText().replace(/([a-z0-9])([A-Z])/g, '$1_$2').toLowerCase().split(/[^a-z0-9]+/);
  return words.some((w) => LOGGER_WORDS.has(w)) ? callee.name.text : null;
}

// Lines holding a `// log-pii: ok <reason>` comment. Comments come from the parser's token positions, so text inside a
// string or template literal never counts.
function annotatedLines(sf) {
  const text = sf.getFullText();
  const seen = new Set();
  const lines = new Set();
  const add = (ranges) => {
    for (const r of ranges || []) {
      if (seen.has(r.pos)) continue;
      seen.add(r.pos);
      if (OK.test(text.slice(r.pos, r.end))) lines.add(sf.getLineAndCharacterOfPosition(r.pos).line);
    }
  };
  const walk = (node) => {
    add(ts.getLeadingCommentRanges(text, node.pos));
    add(ts.getTrailingCommentRanges(text, node.end));
    for (const child of node.getChildren(sf)) walk(child);
  };
  walk(sf);
  return lines;
}

function scanFile(file) {
  const text = fs.readFileSync(file, 'utf8');
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const okLines = annotatedLines(sf);
  const out = [];
  const visit = (node) => {
    if (ts.isCallExpression(node)) {
      const level = logLevel(node);
      if (level) {
        const start = sf.getLineAndCharacterOfPosition(node.getStart(sf));
        for (const arg of node.arguments) {
          for (const leaf of leaves(arg)) {
            const t = leaf.key !== undefined ? [leaf.key, null] : terminal(leaf);
            if (!t) continue;
            const at = leaf.key !== undefined ? leaf.node : leaf;
            const first = sf.getLineAndCharacterOfPosition(at.getStart(sf)).line;
            const last = sf.getLineAndCharacterOfPosition(at.getEnd()).line;
            let ok = false;
            for (let n = first; n <= last; n++) ok = ok || okLines.has(n);
            const expr = (leaf.key !== undefined ? JSON.stringify(leaf.key) : leaf.getText(sf)).replace(/\s+/g, ' ');
            out.push({ path: file, line: start.line + 1, col: start.character, leaf_line: first + 1, level, terminal: t[0],
              owner: t[1], expr, ok, is_key: leaf.key !== undefined });
          }
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return out;
}

for (const f of process.argv.slice(2)) for (const row of scanFile(f)) process.stdout.write(JSON.stringify(row) + '\n');
