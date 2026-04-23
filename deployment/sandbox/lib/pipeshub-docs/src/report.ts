import {
  AlignmentType,
  BorderStyle,
  Document,
  Footer,
  Header,
  HeadingLevel,
  ImageRun,
  LevelFormat,
  Packer,
  PageBreak,
  PageNumber,
  PageOrientation,
  Paragraph,
  ShadingType,
  Table,
  TableCell,
  TableOfContents,
  TableRow,
  TextRun,
  WidthType,
} from "docx";
import * as fs from "fs";
import { DocTheme, DocThemeName, resolveTheme } from "./themes.js";

// ---------------------------------------------------------------------------
// Input types
// ---------------------------------------------------------------------------

export type PaperSize = "letter" | "a4";

export interface ReportOptions {
  theme: DocTheme | DocThemeName | string;
  title?: string;
  subtitle?: string;
  author?: string;
  paperSize?: PaperSize; // default: "letter"
  orientation?: "portrait" | "landscape";
  marginInches?: number; // default 1.0
  headerText?: string;
  showFooterPageNumbers?: boolean; // default true
}

export interface CoverPageOptions {
  title: string;
  subtitle?: string;
  author?: string;
  date?: string;
}

export interface TableOptions {
  headers: string[];
  rows: (string | number)[][];
  columnWidths?: number[]; // in DXA; if omitted, split evenly across content width
}

export interface ImageOptions {
  path: string;
  widthInches: number;
  heightInches: number;
  caption?: string;
}

// 1 inch = 1440 DXA
const DXA_PER_INCH = 1440;

const PAPER: Record<PaperSize, { width: number; height: number }> = {
  letter: { width: 12240, height: 15840 },
  a4: { width: 11906, height: 16838 },
};

// 11 pt body, 24 pt title, 18 pt H1, etc. In docx-js "size" is in half-points.
const HP = (pt: number) => Math.round(pt * 2);

// ---------------------------------------------------------------------------
// Report
// ---------------------------------------------------------------------------

export class Report {
  readonly theme: DocTheme;
  readonly paper: { width: number; height: number };
  readonly marginDxa: number;
  readonly contentWidthDxa: number;
  private readonly opts: ReportOptions;
  private readonly children: (Paragraph | Table | TableOfContents)[] = [];

  constructor(options: ReportOptions) {
    this.theme = resolveTheme(options.theme);
    this.paper = PAPER[options.paperSize ?? "letter"];
    this.marginDxa = Math.round((options.marginInches ?? 1.0) * DXA_PER_INCH);
    this.contentWidthDxa = this.paper.width - this.marginDxa * 2;
    this.opts = options;
  }

  // -------------------------------------------------------------------------
  // Primitives
  // -------------------------------------------------------------------------

  coverPage(options: CoverPageOptions): this {
    const palette = this.theme.palette;
    const type = this.theme.type;

    this.children.push(
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 2400, after: 240 },
        children: [
          new TextRun({
            text: options.title,
            bold: true,
            size: HP(34),
            color: palette.primary,
            font: type.headerFont,
          }),
        ],
      }),
    );
    if (options.subtitle) {
      this.children.push(
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { after: 720 },
          children: [
            new TextRun({
              text: options.subtitle,
              size: HP(18),
              color: palette.textMuted,
              font: type.bodyFont,
            }),
          ],
        }),
      );
    }
    // Thin divider rule, colored
    this.children.push(
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { after: 360 },
        border: {
          bottom: {
            style: BorderStyle.SINGLE,
            size: 8,
            color: palette.accent,
            space: 1,
          },
        },
        children: [new TextRun("")],
      }),
    );

    const authorDate = [options.author, options.date].filter(Boolean).join("   |   ");
    if (authorDate) {
      this.children.push(
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 240, after: 720 },
          children: [
            new TextRun({
              text: authorDate,
              size: HP(12),
              color: palette.textMuted,
              font: type.bodyFont,
            }),
          ],
        }),
      );
    }
    this.pageBreak();
    return this;
  }

  tableOfContents(): this {
    this.children.push(
      new Paragraph({
        heading: HeadingLevel.HEADING_1,
        children: [new TextRun("Table of Contents")],
      }),
    );
    this.children.push(
      new TableOfContents("Table of Contents", {
        hyperlink: true,
        headingStyleRange: "1-3",
      }),
    );
    this.pageBreak();
    return this;
  }

  heading1(text: string): this {
    this.children.push(
      new Paragraph({
        heading: HeadingLevel.HEADING_1,
        spacing: { before: 360, after: 180 },
        children: [new TextRun({ text })],
      }),
    );
    return this;
  }

  heading2(text: string): this {
    this.children.push(
      new Paragraph({
        heading: HeadingLevel.HEADING_2,
        spacing: { before: 240, after: 120 },
        children: [new TextRun({ text })],
      }),
    );
    return this;
  }

  heading3(text: string): this {
    this.children.push(
      new Paragraph({
        heading: HeadingLevel.HEADING_3,
        spacing: { before: 180, after: 120 },
        children: [new TextRun({ text })],
      }),
    );
    return this;
  }

  paragraph(text: string): this {
    const palette = this.theme.palette;
    const type = this.theme.type;
    this.children.push(
      new Paragraph({
        spacing: { after: 160, line: 300 },
        children: [
          new TextRun({
            text,
            size: HP(11),
            color: palette.textPrimary,
            font: type.bodyFont,
          }),
        ],
      }),
    );
    return this;
  }

  bulletList(items: string[]): this {
    const palette = this.theme.palette;
    const type = this.theme.type;
    for (const item of items) {
      this.children.push(
        new Paragraph({
          numbering: { reference: "pipeshub-bullets", level: 0 },
          spacing: { after: 80, line: 300 },
          children: [
            new TextRun({
              text: item,
              size: HP(11),
              color: palette.textPrimary,
              font: type.bodyFont,
            }),
          ],
        }),
      );
    }
    return this;
  }

  numberedList(items: string[]): this {
    const palette = this.theme.palette;
    const type = this.theme.type;
    for (const item of items) {
      this.children.push(
        new Paragraph({
          numbering: { reference: "pipeshub-numbers", level: 0 },
          spacing: { after: 80, line: 300 },
          children: [
            new TextRun({
              text: item,
              size: HP(11),
              color: palette.textPrimary,
              font: type.bodyFont,
            }),
          ],
        }),
      );
    }
    return this;
  }

  calloutBox(text: string): this {
    const palette = this.theme.palette;
    const type = this.theme.type;
    this.children.push(
      new Paragraph({
        indent: { left: 360, right: 360 },
        spacing: { before: 240, after: 240, line: 300 },
        border: {
          left: {
            style: BorderStyle.SINGLE,
            size: 24,
            color: palette.accent,
            space: 12,
          },
        },
        children: [
          new TextRun({
            text,
            italics: true,
            size: HP(12),
            color: palette.textPrimary,
            font: type.bodyFont,
          }),
        ],
      }),
    );
    return this;
  }

  table(options: TableOptions): this {
    const palette = this.theme.palette;
    const type = this.theme.type;
    const cols = options.headers.length;
    const widths =
      options.columnWidths && options.columnWidths.length === cols
        ? options.columnWidths
        : new Array(cols).fill(Math.floor(this.contentWidthDxa / cols));
    const total = widths.reduce((a, b) => a + b, 0);
    // Rescale to exactly contentWidth to satisfy the "sum == table width" invariant.
    const scale = this.contentWidthDxa / total;
    const colWidths = widths.map((w) => Math.floor(w * scale));
    // Correct off-by-one from flooring
    const diff = this.contentWidthDxa - colWidths.reduce((a, b) => a + b, 0);
    colWidths[colWidths.length - 1] += diff;

    const cellBorders = {
      top: { style: BorderStyle.SINGLE, size: 1, color: "D0D7DE" },
      bottom: { style: BorderStyle.SINGLE, size: 1, color: "D0D7DE" },
      left: { style: BorderStyle.SINGLE, size: 1, color: "D0D7DE" },
      right: { style: BorderStyle.SINGLE, size: 1, color: "D0D7DE" },
    };

    const headerRow = new TableRow({
      tableHeader: true,
      children: options.headers.map(
        (h, i) =>
          new TableCell({
            width: { size: colWidths[i], type: WidthType.DXA },
            shading: { fill: palette.primary, type: ShadingType.CLEAR, color: "auto" },
            borders: cellBorders,
            margins: { top: 100, bottom: 100, left: 120, right: 120 },
            children: [
              new Paragraph({
                children: [
                  new TextRun({
                    text: h,
                    bold: true,
                    color: palette.tableHeaderText,
                    size: HP(11),
                    font: type.bodyFont,
                  }),
                ],
              }),
            ],
          }),
      ),
    });

    const dataRows = options.rows.map(
      (row, r) =>
        new TableRow({
          children: row.map(
            (cell, i) =>
              new TableCell({
                width: { size: colWidths[i], type: WidthType.DXA },
                shading:
                  r % 2 === 0
                    ? undefined
                    : { fill: palette.tableZebra, type: ShadingType.CLEAR, color: "auto" },
                borders: cellBorders,
                margins: { top: 80, bottom: 80, left: 120, right: 120 },
                children: [
                  new Paragraph({
                    children: [
                      new TextRun({
                        text: String(cell),
                        size: HP(11),
                        color: palette.textPrimary,
                        font: type.bodyFont,
                      }),
                    ],
                  }),
                ],
              }),
          ),
        }),
    );

    this.children.push(
      new Table({
        width: { size: this.contentWidthDxa, type: WidthType.DXA },
        columnWidths: colWidths,
        rows: [headerRow, ...dataRows],
      }),
    );
    this.children.push(
      new Paragraph({ spacing: { after: 180 }, children: [new TextRun("")] }),
    );
    return this;
  }

  image(options: ImageOptions): this {
    const palette = this.theme.palette;
    const type = this.theme.type;
    const data = fs.readFileSync(options.path);
    const extMatch = options.path.toLowerCase().match(/\.(png|jpg|jpeg|gif|bmp|svg)$/);
    const ext = (extMatch ? extMatch[1] : "png") as "png" | "jpg" | "jpeg" | "gif" | "bmp" | "svg";
    this.children.push(
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 240, after: options.caption ? 80 : 240 },
        children: [
          // docx's `IImageOptions` is a discriminated union: SVG requires a
          // `fallback` raster image, other formats don't. TypeScript can't
          // narrow from a runtime `ext` value, so we cast — the shape is
          // still correct at runtime.
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          new ImageRun({
            type: ext === "jpeg" ? "jpg" : ext,
            data,
            transformation: {
              width: Math.round(options.widthInches * 96),
              height: Math.round(options.heightInches * 96),
            },
          } as any),
        ],
      }),
    );
    if (options.caption) {
      this.children.push(
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { after: 240 },
          children: [
            new TextRun({
              text: options.caption,
              italics: true,
              size: HP(10),
              color: palette.textMuted,
              font: type.bodyFont,
            }),
          ],
        }),
      );
    }
    return this;
  }

  pageBreak(): this {
    this.children.push(
      new Paragraph({ children: [new PageBreak()] }),
    );
    return this;
  }

  async save(outPath: string): Promise<void> {
    const doc = this.buildDocument();
    const buffer = await Packer.toBuffer(doc);
    fs.writeFileSync(outPath, buffer);
  }

  // -------------------------------------------------------------------------
  // Internals
  // -------------------------------------------------------------------------

  private buildDocument(): Document {
    const palette = this.theme.palette;
    const type = this.theme.type;

    return new Document({
      creator: this.opts.author ?? "pipeshub",
      title: this.opts.title,
      description: this.opts.subtitle,
      styles: {
        default: {
          document: {
            run: { font: type.bodyFont, size: HP(11), color: palette.textPrimary },
          },
        },
        paragraphStyles: [
          {
            id: "Heading1",
            name: "Heading 1",
            basedOn: "Normal",
            next: "Normal",
            quickFormat: true,
            run: { size: HP(20), bold: true, color: palette.primary, font: type.headerFont },
            paragraph: { spacing: { before: 360, after: 180 }, outlineLevel: 0 },
          },
          {
            id: "Heading2",
            name: "Heading 2",
            basedOn: "Normal",
            next: "Normal",
            quickFormat: true,
            run: { size: HP(16), bold: true, color: palette.primary, font: type.headerFont },
            paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 1 },
          },
          {
            id: "Heading3",
            name: "Heading 3",
            basedOn: "Normal",
            next: "Normal",
            quickFormat: true,
            run: { size: HP(13), bold: true, color: palette.primary, font: type.headerFont },
            paragraph: { spacing: { before: 180, after: 100 }, outlineLevel: 2 },
          },
        ],
      },
      numbering: {
        config: [
          {
            reference: "pipeshub-bullets",
            levels: [
              {
                level: 0,
                format: LevelFormat.BULLET,
                text: "\u2022",
                alignment: AlignmentType.LEFT,
                style: { paragraph: { indent: { left: 720, hanging: 360 } } },
              },
              {
                level: 1,
                format: LevelFormat.BULLET,
                text: "\u25E6",
                alignment: AlignmentType.LEFT,
                style: { paragraph: { indent: { left: 1440, hanging: 360 } } },
              },
            ],
          },
          {
            reference: "pipeshub-numbers",
            levels: [
              {
                level: 0,
                format: LevelFormat.DECIMAL,
                text: "%1.",
                alignment: AlignmentType.LEFT,
                style: { paragraph: { indent: { left: 720, hanging: 360 } } },
              },
            ],
          },
        ],
      },
      sections: [
        {
          properties: {
            page: {
              size: {
                width: this.paper.width,
                height: this.paper.height,
                orientation:
                  this.opts.orientation === "landscape"
                    ? PageOrientation.LANDSCAPE
                    : PageOrientation.PORTRAIT,
              },
              margin: {
                top: this.marginDxa,
                right: this.marginDxa,
                bottom: this.marginDxa,
                left: this.marginDxa,
              },
            },
          },
          headers: this.opts.headerText
            ? {
                default: new Header({
                  children: [
                    new Paragraph({
                      alignment: AlignmentType.RIGHT,
                      border: {
                        bottom: {
                          style: BorderStyle.SINGLE,
                          size: 6,
                          color: palette.primary,
                          space: 4,
                        },
                      },
                      children: [
                        new TextRun({
                          text: this.opts.headerText,
                          color: palette.textMuted,
                          size: HP(9),
                          font: type.bodyFont,
                        }),
                      ],
                    }),
                  ],
                }),
              }
            : undefined,
          footers:
            (this.opts.showFooterPageNumbers ?? true)
              ? {
                  default: new Footer({
                    children: [
                      new Paragraph({
                        alignment: AlignmentType.CENTER,
                        children: [
                          new TextRun({
                            text: "Page ",
                            color: palette.textMuted,
                            size: HP(9),
                            font: type.bodyFont,
                          }),
                          new TextRun({
                            children: [PageNumber.CURRENT],
                            color: palette.textMuted,
                            size: HP(9),
                            font: type.bodyFont,
                          }),
                          new TextRun({
                            text: " of ",
                            color: palette.textMuted,
                            size: HP(9),
                            font: type.bodyFont,
                          }),
                          new TextRun({
                            children: [PageNumber.TOTAL_PAGES],
                            color: palette.textMuted,
                            size: HP(9),
                            font: type.bodyFont,
                          }),
                        ],
                      }),
                    ],
                  }),
                }
              : undefined,
          children: this.children,
        },
      ],
    });
  }
}
