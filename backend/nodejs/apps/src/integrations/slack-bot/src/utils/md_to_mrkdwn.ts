/**
 * Converts standard Markdown to Slack-compatible mrkdwn format.
 *
 * Key transformations:
 *  - **bold** / __bold__        → *bold*
 *  - *italic* / _italic_        → _italic_
 *  - ~~strikethrough~~          → ~strikethrough~
 *  - [text](url)                → <url|text>
 *  - ![alt](url)                → <url|alt>
 *  - # Headings                 → *Headings* (bolded)
 *  - > blockquotes              → > blockquotes (preserved)
 *  - `inline code`              → `inline code` (preserved)
 *  - ```code blocks```          → ```code blocks``` (preserved)
 *  - Unordered lists (- / *)    → • bullet
 *  - Ordered lists              → preserved with number
 *  - Horizontal rules           → ———
 *  - Markdown tables            → aligned code block or plain text
 *  - HTML tags                  → stripped
 *
 * Code spans and code blocks are protected from transformation.
 */

interface ConvertOptions {
    /** Preserve original link markdown instead of converting to Slack format */
    preserveLinks?: boolean;
    /** Custom bullet character (default: "•") */
    bulletChar?: string;
    /**
     * How to render tables in Slack:
     * - "code"   → monospaced code block with aligned columns (default)
     * - "text"   → plain text with bold headers and column separators
     */
    tableMode?: "code" | "text";
  }
  
  // ── Table helpers ──────────────────────────────────────────────────────
  
  /** Returns true if a line looks like a markdown table row: | col | col | */
  function isTableRow(line: string): boolean {
    return /^\s*\|.*\|\s*$/.test(line);
  }
  
  /** Returns true if a line is the separator row: | --- | :---: | ---: | */
  function isSeparatorRow(line: string): boolean {
    return /^\s*\|[\s:]*-{3,}[\s:]*(\|[\s:]*-{3,}[\s:]*)*\|\s*$/.test(line);
  }
  
  /** Parse a table row into trimmed cell strings */
  function parseCells(row: string): string[] {
    return row
      .replace(/^\s*\|/, "")
      .replace(/\|\s*$/, "")
      .split("|")
      .map((c) => c.trim());
  }
  
  /** Detect column alignment from the separator row */
  function parseAlignments(sepRow: string): ("left" | "center" | "right")[] {
    return parseCells(sepRow).map((cell) => {
      const left = cell.startsWith(":");
      const right = cell.endsWith(":");
      if (left && right) return "center";
      if (right) return "right";
      return "left";
    });
  }
  
  /** Pad a string to a given width respecting alignment */
  function padCell(text: string, width: number, align: "left" | "center" | "right"): string {
    if (align === "right") return text.padStart(width);
    if (align === "center") {
      const total = width - text.length;
      const left = Math.floor(total / 2);
      return " ".repeat(left) + text + " ".repeat(total - left);
    }
    return text.padEnd(width);
  }
  
  /**
   * Find consecutive table lines in the text, parse them, and replace
   * with either a code-block or plain-text representation.
   */
  function convertTables(
    text: string,
    placeholderFn: (content: string) => string,
    mode: "code" | "text" = "code"
  ): string {
    const lines = text.split("\n");
    const result: string[] = [];
    let i = 0;
  
    while (i < lines.length) {
      const headerLine = lines[i];
      const separatorLine = lines[i + 1];
      // Need at least: header row, separator row, one data row
      if (
        headerLine !== undefined &&
        separatorLine !== undefined &&
        isTableRow(headerLine) &&
        isSeparatorRow(separatorLine)
      ) {
        // Collect all consecutive table rows
        const headerCells = parseCells(headerLine);
        const alignments = parseAlignments(separatorLine);
        const dataRows: string[][] = [];
        i += 2; // skip header + separator
        while (i < lines.length) {
          const rowLine = lines[i];
          if (rowLine === undefined || !isTableRow(rowLine)) break;
          dataRows.push(parseCells(rowLine));
          i++;
        }
  
        const allRows = [headerCells, ...dataRows];
        const colCount = Math.max(...allRows.map((r) => r.length));
  
        // Normalize row lengths
        for (const row of allRows) {
          while (row.length < colCount) row.push("");
        }
        // Ensure alignments array matches
        while (alignments.length < colCount) alignments.push("left");
  
        // Compute column widths
        const colWidths = Array.from({ length: colCount }, (_, ci) =>
          Math.max(...allRows.map((r) => (r[ci] ?? "").length))
        );
  
        if (mode === "code") {
          // ── Code block table ──
          const rendered: string[] = [];
          // Header
          rendered.push(
            "| " +
              headerCells
                .map((c, ci) => padCell(c, colWidths[ci] ?? 0, alignments[ci] ?? "left"))
                .join(" | ") +
              " |"
          );
          // Separator
          rendered.push(
            "| " +
              colWidths.map((w) => "-".repeat(w)).join(" | ") +
              " |"
          );
          // Data rows
          for (const row of dataRows) {
            rendered.push(
              "| " +
                row
                  .map((c, ci) => padCell(c, colWidths[ci] ?? 0, alignments[ci] ?? "left"))
                  .join(" | ") +
                " |"
            );
          }
          result.push(placeholderFn("```\n" + rendered.join("\n") + "\n```"));
        } else {
          // ── Plain text table ──
          // *Header1* | *Header2* | *Header3*
          // Value1    | Value2    | Value3
          const headerLine = headerCells
            .map((c, ci) => `*${padCell(c, colWidths[ci] ?? 0, alignments[ci] ?? "left").trim()}*`)
            .join("  |  ");
          result.push(placeholderFn(headerLine));
          for (const row of dataRows) {
            const dataLine = row
              .map((c, ci) => padCell(c, colWidths[ci] ?? 0, alignments[ci] ?? "left").trim())
              .join("  |  ");
            result.push(placeholderFn(dataLine));
          }
        }
      } else {
        result.push(lines[i] ?? "");
        i++;
      }
    }
  
    return result.join("\n");
  }
  
  export function markdownToSlackMrkdwn(
    markdown: string,
    options: ConvertOptions = {}
  ): string {
    const { preserveLinks = false, bulletChar = "•", tableMode = "code" } = options;
  
    if (!markdown) return "";
  
    // ── Step 1: Extract and protect code blocks & inline code ──────────
    // We replace them with placeholders so regex transforms don't touch them.
  
    const placeholders: string[] = [];
  
    function placeholder(content: string): string {
      const idx = placeholders.length;
      placeholders.push(content);
      return `\x00PLACEHOLDER_${idx}\x00`;
    }
  
    // Protect fenced code blocks (``` ... ```)
    let text = markdown.replace(
      /```(\w*)\n([\s\S]*?)```/g,
      (_match, _lang, code) => placeholder("```\n" + code.trimEnd() + "\n```")
    );
  
    // Protect inline code (` ... `)
    text = text.replace(/`([^`\n]+)`/g, (_match, code) =>
      placeholder("`" + code + "`")
    );
  
    // ── Step 1b: Convert Markdown tables → code block (monospaced) ─────
    // Slack has no table syntax, so we render as an aligned code block.
  
    text = convertTables(text, placeholder, tableMode);
  
    // ── Step 2: Line-level transformations ─────────────────────────────
  
    const lines = text.split("\n");
    const transformed: string[] = [];
  
    for (let i = 0; i < lines.length; i++) {
      let line = lines[i] ?? "";
  
      // --- Horizontal rules ---
      if (/^\s*(?:-\s*){3,}$|^\s*(?:\*\s*){3,}$|^\s*(?:_\s*){3,}$/.test(line)) {
        transformed.push("———");
        continue;
      }
  
      // --- Headings (# … ######) → *bold text* ---
      const headingMatch = line.match(/^(#{1,6})\s+(.+?)(\s*#*\s*)$/);
      if (headingMatch) {
        transformed.push(placeholder(`*${(headingMatch[2] ?? "").trim()}*`));
        continue;
      }
  
      // --- Unordered list items (-, *, +) → • ---
      const ulMatch = line.match(/^(\s*)[-*+]\s+(.*)/);
      if (ulMatch) {
        const indent = (ulMatch[1] ?? "").replace(/\t/g, "    ");
        // Nest with 2-space indentation per level
        const level = Math.floor(indent.length / 2);
        const prefix = "  ".repeat(level) + bulletChar + " ";
        line = prefix + (ulMatch[2] ?? "");
      }
  
      // --- Ordered list items: keep numbering, just normalize ---
      const olMatch = line.match(/^(\s*)\d+[.)]\s+(.*)/);
      if (olMatch && !ulMatch) {
        const indent = (olMatch[1] ?? "").replace(/\t/g, "    ");
        const level = Math.floor(indent.length / 2);
        // Re-derive the visual number (Slack doesn't auto-number)
        const prefix = "  ".repeat(level);
        line = prefix + line.trimStart();
      }
  
      // --- Blockquotes: > text (Slack uses the same syntax) ---
      // Ensure exactly one space after >
      line = line.replace(/^(\s*)>\s?/, "$1> ");
  
      transformed.push(line);
    }
  
    text = transformed.join("\n");
  
    // ── Step 3: Inline transformations ─────────────────────────────────
  
    // Images: ![alt](url) → <url|alt>
    if (!preserveLinks) {
      text = text.replace(
        /!\[([^\]]*)\]\((\S+?)(?:\s+"[^"]*")?\)/g,
        (_m, alt, url) => (alt ? `<${url}|${alt}>` : `<${url}>`)
      );
    }
  
    // Links: [text](url) → <url|text>
    if (!preserveLinks) {
      text = text.replace(
        /\[([^\]]+)\]\((\S+?)(?:\s+"[^"]*")?\)/g,
        (_m, label, url) => `<${url}|${label}>`
      );
    }
  
    // Autolinks: <http...> are already Slack-compatible, leave them.
  
    // Bold + Italic (***text*** or ___text___) → *_text_*
    text = text.replace(/\*{3}(.+?)\*{3}/g, (_m, c) => placeholder("*_" + c + "_*"));
    text = text.replace(/_{3}(.+?)_{3}/g, (_m, c) => placeholder("*_" + c + "_*"));
  
    // Bold: **text** or __text__ → *text*  (placeholder-protect the result)
    text = text.replace(/\*{2}(.+?)\*{2}/g, (_m, c) => placeholder("*" + c + "*"));
    text = text.replace(/_{2}(.+?)_{2}/g, (_m, c) => placeholder("*" + c + "*"));
  
    // Italic: *text* → _text_  (single asterisks only, now safe since bold is placeholdered)
    text = text.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, "_$1_");
  
    // Strikethrough: ~~text~~ → ~text~
    text = text.replace(/~~(.+?)~~/g, "~$1~");
  
    // Strip simple HTML tags (e.g. <br>, <p>, <em>, etc.)
    text = text.replace(/<\/?(br|p|div|span|em|strong|b|i|u|s|del|hr)\s*\/?>/gi, "");
  
    // ── Step 4: Restore placeholders ───────────────────────────────────
  
    text = text.replace(/\x00PLACEHOLDER_(\d+)\x00/g, (_m, idx) => placeholders[+idx] ?? "");
  
    // ── Step 5: Clean up excess blank lines ────────────────────────────
  
    text = text.replace(/\n{3,}/g, "\n\n");
  
    return text.trim();
  }
  
  export default markdownToSlackMrkdwn;
  