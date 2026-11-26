/**
 * QAFINAL-FU-AXE-2: `FormField` draws a `<label htmlFor>` and hands its control ids down through context, but a raw
 * `TextField.Root`, `<input>` or `Select.Trigger` placed inside it never reads them, so the label names nothing and
 * the help and error text describe nothing (axe `label`; 23 files did this). This reads every FormField in the app
 * and fails on a control inside one that is not named by it, so a new one cannot land unlabelled.
 */
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import ts from 'typescript';
import { describe, expect, it } from 'vitest';

const FRONTEND = path.resolve(__dirname, '../../../../..');

/** Elements a person types into, picks with or toggles: each needs a name. */
const CONTROLS = new Set([
  'input',
  'textarea',
  'select',
  'TextField.Root',
  'TextArea',
  'Select.Trigger',
  'Checkbox',
  'Switch',
  'RadioGroup.Root',
  'Slider',
  'SegmentedControl.Root',
]);
/** Controls that read the FormField's ids themselves. */
const LABELLED_BY_FIELD = new Set(['FormFieldTextField', 'FormFieldTextArea', 'FormFieldSelectTrigger']);
/** Hooks and wrappers whose use in a component's file means it reads the FormField's ids. */
const READS_FIELD = /\buseFormFieldControl\b|\bFormFieldTextField\b|\bFormFieldTextArea\b|\bFormFieldSelectTrigger\b/;
/** Names a control carries on its own. */
const OWN_NAME = new Set(['aria-label', 'aria-labelledby', 'ariaLabel']);

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const at = path.join(dir, name);
    if (statSync(at).isDirectory()) return name === '__tests__' || name === 'node_modules' ? [] : sourceFiles(at);
    return at.endsWith('.tsx') ? [at] : [];
  });
}

function parse(file: string, text = readFileSync(file, 'utf8')): ts.SourceFile {
  return ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
}

type Opening = ts.JsxOpeningElement | ts.JsxSelfClosingElement;

function opening(node: ts.Node): Opening | null {
  if (ts.isJsxElement(node)) return node.openingElement;
  if (ts.isJsxSelfClosingElement(node)) return node;
  return null;
}

function attr(el: Opening, name: string): ts.JsxAttribute | undefined {
  return el.attributes.properties.find(
    (p): p is ts.JsxAttribute => ts.isJsxAttribute(p) && p.name.getText() === name,
  );
}

function attrText(el: Opening, name: string): string | undefined {
  const a = attr(el, name);
  if (!a?.initializer) return undefined;
  return ts.isStringLiteral(a.initializer) ? a.initializer.text : a.initializer.getText();
}

/** Not in the accessibility tree, so it needs no name: a file input opened by a named button, a hidden value. */
function hidden(el: Opening): boolean {
  if (attr(el, 'hidden') || attrText(el, 'type') === 'hidden' || attrText(el, 'aria-hidden') === 'true') return true;
  return /display:\s*['"]none['"]/.test(attrText(el, 'style') ?? '');
}

function insideLabel(node: ts.Node, field: ts.Node): boolean {
  for (let at = node.parent; at && at !== field; at = at.parent) {
    const el = opening(at);
    if (!el) continue;
    if (el.tagName.getText() === 'label' || attrText(el, 'as') === 'label') return true;
  }
  return false;
}

/** The names a FormField's render-prop child binds its control ids to (`{(control) => <input {...control} />}`). */
function renderPropNames(field: ts.JsxElement): Set<string> {
  const names = new Set<string>();
  for (const child of field.children) {
    if (!ts.isJsxExpression(child) || !child.expression) continue;
    const fn = child.expression;
    if ((ts.isArrowFunction(fn) || ts.isFunctionExpression(fn)) && fn.parameters[0]) {
      names.add(fn.parameters[0].name.getText());
    }
  }
  return names;
}

function spreadsFieldIds(el: Opening, bound: Set<string>): boolean {
  return el.attributes.properties.some((p) => {
    if (!ts.isJsxSpreadAttribute(p)) return false;
    const text = p.expression.getText();
    return text === 'useFormFieldControl()' || bound.has(text);
  });
}

/** The declaration of a component used in `file`: in the same file, or where it is imported from (through barrels). */
function declaration(file: string, sf: ts.SourceFile, name: string, seen = new Set<string>()): ts.Node | null {
  if (seen.has(file)) return null;
  seen.add(file);
  for (const stmt of sf.statements) {
    if (ts.isFunctionDeclaration(stmt) && stmt.name?.text === name) return stmt;
    if (ts.isVariableStatement(stmt)) {
      const decl = stmt.declarationList.declarations.find((d) => d.name.getText() === name);
      if (decl) return decl;
    }
  }
  for (const stmt of sf.statements) {
    const isImport = ts.isImportDeclaration(stmt);
    const isReexport = ts.isExportDeclaration(stmt) && !!stmt.moduleSpecifier;
    if (!isImport && !isReexport) continue;
    const spec = (stmt.moduleSpecifier as ts.StringLiteral).text;
    let names: string[] = [];
    if (isImport) {
      const bindings = stmt.importClause?.namedBindings;
      if (bindings && ts.isNamedImports(bindings)) names = bindings.elements.map((e) => e.name.text);
      if (stmt.importClause?.name) names.push(stmt.importClause.name.text);
    } else {
      const clause = (stmt as ts.ExportDeclaration).exportClause;
      names = clause && ts.isNamedExports(clause) ? clause.elements.map((e) => e.name.text) : [name];
    }
    if (!names.includes(name)) continue;
    const target = resolve(file, spec);
    if (!target) continue;
    const found = declaration(target, parse(target), name, seen);
    if (found) return found;
  }
  return null;
}

function resolve(from: string, spec: string): string | null {
  let base: string;
  if (spec.startsWith('@/')) base = path.join(FRONTEND, spec.slice(2));
  else if (spec.startsWith('.')) base = path.resolve(path.dirname(from), spec);
  else return null;
  for (const candidate of [`${base}.tsx`, `${base}.ts`, path.join(base, 'index.ts'), path.join(base, 'index.tsx')]) {
    if (existsSync(candidate) && statSync(candidate).isFile()) return candidate;
  }
  return null;
}

/** A component that draws an unnamed control without reading the FormField's ids leaves that control unnamed. */
function drawsUnnamedControl(decl: ts.Node): boolean {
  if (READS_FIELD.test(decl.getText())) return false;
  let found = false;
  const visit = (node: ts.Node) => {
    const el = opening(node);
    if (el && CONTROLS.has(el.tagName.getText()) && !hidden(el) && ![...OWN_NAME].some((n) => attr(el, n))) {
      found = true;
    }
    if (!found) ts.forEachChild(node, visit);
  };
  visit(decl);
  return found;
}

/** `file:line Tag` for each control inside a FormField that the field's label does not name. */
export function unlabelledControls(file: string, text?: string): string[] {
  const sf = parse(file, text);
  const out: string[] = [];
  const where = (node: ts.Node) => `${path.relative(FRONTEND, file)}:${sf.getLineAndCharacterOfPosition(node.getStart()).line + 1}`;

  const checkField = (field: ts.JsxElement) => {
    const bound = renderPropNames(field);
    const visit = (node: ts.Node) => {
      if (node !== field && ts.isJsxElement(node) && node.openingElement.tagName.getText() === 'FormField') return;
      const el = opening(node);
      if (el) {
        const tag = el.tagName.getText();
        const named =
          LABELLED_BY_FIELD.has(tag) ||
          [...OWN_NAME].some((n) => attr(el, n)) ||
          spreadsFieldIds(el, bound) ||
          hidden(el) ||
          insideLabel(node, field);
        if (CONTROLS.has(tag) && !named) out.push(`${where(el)} ${tag}`);
        if (/^[A-Z]\w*$/.test(tag) && !LABELLED_BY_FIELD.has(tag) && !named) {
          const decl = declaration(file, sf, tag);
          if (decl && drawsUnnamedControl(decl)) {
            out.push(`${where(el)} ${tag} (${path.relative(FRONTEND, decl.getSourceFile().fileName)})`);
          }
        }
      }
      ts.forEachChild(node, visit);
    };
    ts.forEachChild(field, visit);
  };

  const find = (node: ts.Node) => {
    if (ts.isJsxElement(node) && node.openingElement.tagName.getText() === 'FormField') checkField(node);
    ts.forEachChild(node, find);
  };
  find(sf);
  return out;
}

describe('FormField controls are named by their label', () => {
  it('FFLABEL-1: no control inside a FormField anywhere in the app is left without its label', () => {
    const found = [...sourceFiles(path.join(FRONTEND, 'app')), ...sourceFiles(path.join(FRONTEND, 'lib'))]
      .filter((f) => readFileSync(f, 'utf8').includes('<FormField'))
      .flatMap((f) => unlabelledControls(f));
    expect(found).toEqual([]);
  });

  it('FFLABEL-2: the check finds a raw input and a raw Select trigger, and accepts the wired forms', () => {
    const file = path.join(FRONTEND, 'app/x.tsx');
    const src = (body: string) => `const X = () => (<FormField label="L">${body}</FormField>);`;
    expect(unlabelledControls(file, src('<TextField.Root value={v} />'))).toHaveLength(1);
    expect(unlabelledControls(file, src('<Select.Root><Select.Trigger /></Select.Root>'))).toHaveLength(1);
    expect(unlabelledControls(file, src('<Flex>{ok && <input type="text" />}</Flex>'))).toHaveLength(1);
    expect(unlabelledControls(file, src('<FormFieldTextField value={v} />'))).toEqual([]);
    expect(unlabelledControls(file, src('{(control) => <input {...control} />}'))).toEqual([]);
    expect(unlabelledControls(file, src('<input aria-label="Name" />'))).toEqual([]);
    expect(unlabelledControls(file, src('<input type="file" hidden />'))).toEqual([]);
    expect(unlabelledControls(file, src('<Text as="label"><Checkbox /> Yes</Text>'))).toEqual([]);
    const own = `function Raw() { return <input />; }\nfunction Wired() { return <input {...useFormFieldControl()} />; }\n`;
    expect(unlabelledControls(file, own + src('<Raw />'))).toEqual([expect.stringMatching(/:3 Raw/)]);
    expect(unlabelledControls(file, own + src('<Wired />'))).toEqual([]);
  });
});
