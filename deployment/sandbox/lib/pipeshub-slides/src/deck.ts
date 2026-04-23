// pptxgenjs is a CJS module with a class default export merged with a
// type-only namespace. NodeNext + ESM output makes the type plumbing noisy,
// so we use `createRequire` for the runtime value (which always gives the
// real class) and a local type alias for the type side. The UMD-merged
// namespace types (`Slide`, `ImageProps`, `ChartData`) come from a triple-
// slash reference which doesn't go through ES module resolution.
/// <reference types="pptxgenjs" />
import { createRequire } from "module";
// eslint-disable-next-line @typescript-eslint/no-explicit-any, @typescript-eslint/no-require-imports
const PptxGenJS: any = createRequire(import.meta.url)("pptxgenjs");
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type pptxgen = any;
// eslint-disable-next-line @typescript-eslint/no-namespace
namespace pptxgen {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export type Slide = any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export type ImageProps = any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export type ChartData = any;
}
import {
  ACCENT_BAND_HEIGHT,
  BODY_Y,
  LAYOUTS,
  LayoutName,
  LayoutDimensions,
  MARGINS,
  TITLE_HEIGHT,
  TITLE_Y,
} from "./layout.js";
import { Theme, ThemeName, resolveTheme } from "./themes.js";

// ---------------------------------------------------------------------------
// Input types
// ---------------------------------------------------------------------------

export interface DeckOptions {
  theme: Theme | ThemeName | string;
  layout?: LayoutName;
  title?: string;
  author?: string;
  company?: string;
  showAccentBand?: boolean; // default true
  showFooter?: boolean; // default true on content slides
}

export interface TitleSlideOptions {
  title: string;
  subtitle?: string;
  eyebrow?: string;
  author?: string;
  date?: string;
  // If supplied, rendered as a half-bleed image on the right. Aspect ratio is
  // preserved by cover-fitting.
  heroImagePath?: string;
}

export interface BulletContent {
  bullets: string[];
}

export interface ParagraphContent {
  paragraph: string;
}

export interface ImageContent {
  imagePath?: string;
  imageData?: string; // base64, as pptxgenjs accepts
  caption?: string;
}

export interface ChartContent {
  chart: {
    type: "bar" | "line" | "pie" | "doughnut";
    categories: string[];
    series: { name: string; values: number[] }[];
  };
}

export type ColumnContent =
  | BulletContent
  | ParagraphContent
  | ImageContent
  | ChartContent;

export interface TwoColumnOptions {
  title: string;
  left: ColumnContent;
  right: ColumnContent;
  // 60/40 (default) puts the text on the side with 60% and visual on 40%.
  split?: 50 | 60 | 40;
}

export interface Stat {
  value: string;
  label: string;
}
export interface StatGridOptions {
  title: string;
  stats: Stat[]; // 2-6 recommended
}

export interface IconRow {
  icon?: string; // e.g. "FaRocket" - optional, rendered as a colored circle with the first letter if the icon library isn't available
  header: string;
  body: string;
}
export interface IconRowsOptions {
  title: string;
  rows: IconRow[]; // 3-5
}

export interface TimelineStep {
  label: string;
  description?: string;
}
export interface TimelineOptions {
  title: string;
  steps: TimelineStep[]; // 3-5
}

export interface SectionDividerOptions {
  eyebrow?: string;
  title: string;
  subtitle?: string;
}

export interface ClosingOptions {
  title: string;
  subtitle?: string;
  cta?: string;
}

// ---------------------------------------------------------------------------
// Deck
// ---------------------------------------------------------------------------

export class Deck {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  readonly pres: any;
  readonly theme: Theme;
  readonly dim: LayoutDimensions;
  private readonly opts: Required<
    Pick<DeckOptions, "showAccentBand" | "showFooter">
  > & { title: string; author: string; company: string };
  private slideIndex = 0;

  constructor(options: DeckOptions) {
    this.theme = resolveTheme(options.theme);
    this.dim = LAYOUTS[options.layout ?? "16x9"];

    const pres = new PptxGenJS();
    pres.layout = this.dim.pptxName;
    if (options.title) pres.title = options.title;
    if (options.author) pres.author = options.author;
    if (options.company) pres.company = options.company;

    this.pres = pres;
    this.opts = {
      showAccentBand: options.showAccentBand ?? true,
      showFooter: options.showFooter ?? true,
      title: options.title ?? "",
      author: options.author ?? "",
      company: options.company ?? "",
    };
  }

  // -------------------------------------------------------------------------
  // Primitives
  // -------------------------------------------------------------------------

  titleSlide(options: TitleSlideOptions): this {
    const slide = this.pres.addSlide();
    slide.background = { color: this.theme.palette.primary };
    const palette = this.theme.palette;
    const type = this.theme.type;

    if (options.eyebrow) {
      slide.addText(options.eyebrow.toUpperCase(), {
        x: MARGINS.left,
        y: this.dim.height * 0.18,
        w: this.dim.width - MARGINS.left - MARGINS.right,
        h: 0.4,
        fontSize: 14,
        fontFace: type.headerFont,
        bold: true,
        color: palette.accent,
        charSpacing: 4,
        margin: 0,
      });
    }

    slide.addText(options.title, {
      x: MARGINS.left,
      y: this.dim.height * 0.3,
      w: this.dim.width - MARGINS.left - MARGINS.right,
      h: 1.6,
      fontSize: Math.max(44, type.titleSize + 4),
      fontFace: type.headerFont,
      bold: true,
      color: palette.accent,
      margin: 0,
    });

    if (options.subtitle) {
      slide.addText(options.subtitle, {
        x: MARGINS.left,
        y: this.dim.height * 0.55,
        w: this.dim.width - MARGINS.left - MARGINS.right,
        h: 1,
        fontSize: Math.max(22, type.headerSize + 2),
        fontFace: type.bodyFont,
        color: palette.secondary,
        margin: 0,
      });
    }

    const authorDate = [options.author, options.date].filter(Boolean).join(" \u2022 ");
    if (authorDate) {
      slide.addText(authorDate, {
        x: MARGINS.left,
        y: this.dim.height - MARGINS.bottom - 0.4,
        w: this.dim.width - MARGINS.left - MARGINS.right,
        h: 0.35,
        fontSize: type.captionSize,
        fontFace: type.bodyFont,
        color: palette.secondary,
        margin: 0,
      });
    }

    // Subtle accent block in the bottom-right corner as a visual motif.
    slide.addShape(this.pres.shapes.RECTANGLE, {
      x: this.dim.width - 1.2,
      y: this.dim.height - 0.12,
      w: 1.2,
      h: 0.12,
      fill: { color: palette.accent },
      line: { type: "none" },
    });

    this.slideIndex += 1;
    return this;
  }

  contentSlide(title: string, render: (slide: pptxgen.Slide) => void): this {
    const slide = this.pres.addSlide();
    slide.background = { color: this.theme.palette.background };
    this.drawChrome(slide, title);
    render(slide);
    this.slideIndex += 1;
    return this;
  }

  twoColumn(options: TwoColumnOptions): this {
    return this.contentSlide(options.title, (slide) => {
      const usableW = this.dim.width - MARGINS.left - MARGINS.right;
      const gutter = 0.4;
      const leftW = ((options.split ?? 60) / 100) * (usableW - gutter);
      const rightW = usableW - gutter - leftW;
      const bodyH = this.dim.height - BODY_Y - MARGINS.bottom - 0.3;
      this.renderColumn(
        slide,
        options.left,
        MARGINS.left,
        BODY_Y,
        leftW,
        bodyH,
      );
      this.renderColumn(
        slide,
        options.right,
        MARGINS.left + leftW + gutter,
        BODY_Y,
        rightW,
        bodyH,
      );
    });
  }

  statGrid(options: StatGridOptions): this {
    return this.contentSlide(options.title, (slide) => {
      const n = Math.max(1, Math.min(options.stats.length, 6));
      const cols = n <= 3 ? n : 3;
      const rows = Math.ceil(n / cols);
      const usableW = this.dim.width - MARGINS.left - MARGINS.right;
      const usableH =
        this.dim.height - BODY_Y - MARGINS.bottom - 0.2;
      const gap = 0.3;
      const cardW = (usableW - gap * (cols - 1)) / cols;
      const cardH = (usableH - gap * (rows - 1)) / rows;
      const palette = this.theme.palette;
      const type = this.theme.type;

      options.stats.slice(0, cols * rows).forEach((stat, i) => {
        const r = Math.floor(i / cols);
        const c = i % cols;
        const x = MARGINS.left + c * (cardW + gap);
        const y = BODY_Y + r * (cardH + gap);

        slide.addShape(this.pres.shapes.RECTANGLE, {
          x,
          y,
          w: cardW,
          h: cardH,
          fill: { color: palette.surface },
          line: { type: "none" },
        });
        slide.addShape(this.pres.shapes.RECTANGLE, {
          x,
          y,
          w: 0.08,
          h: cardH,
          fill: { color: palette.primary },
          line: { type: "none" },
        });
        slide.addText(stat.value, {
          x: x + 0.25,
          y: y + cardH * 0.18,
          w: cardW - 0.4,
          h: cardH * 0.5,
          fontSize: Math.max(36, Math.round(cardH * 80)),
          bold: true,
          fontFace: type.headerFont,
          color: palette.primary,
          margin: 0,
        });
        slide.addText(stat.label, {
          x: x + 0.25,
          y: y + cardH * 0.65,
          w: cardW - 0.4,
          h: cardH * 0.3,
          fontSize: type.bodySize - 2,
          fontFace: type.bodyFont,
          color: palette.textMuted,
          margin: 0,
        });
      });
    });
  }

  iconRows(options: IconRowsOptions): this {
    return this.contentSlide(options.title, (slide) => {
      const palette = this.theme.palette;
      const type = this.theme.type;
      const n = Math.max(1, Math.min(options.rows.length, 6));
      const usableW = this.dim.width - MARGINS.left - MARGINS.right;
      const usableH = this.dim.height - BODY_Y - MARGINS.bottom - 0.2;
      const gap = 0.2;
      const rowH = (usableH - gap * (n - 1)) / n;
      const iconSize = Math.min(rowH - 0.15, 0.7);

      options.rows.slice(0, n).forEach((row, i) => {
        const y = BODY_Y + i * (rowH + gap);
        slide.addShape(this.pres.shapes.OVAL, {
          x: MARGINS.left,
          y: y + (rowH - iconSize) / 2,
          w: iconSize,
          h: iconSize,
          fill: { color: palette.primary },
          line: { type: "none" },
        });
        const initial = (row.icon ?? row.header).slice(0, 1).toUpperCase();
        slide.addText(initial, {
          x: MARGINS.left,
          y: y + (rowH - iconSize) / 2,
          w: iconSize,
          h: iconSize,
          fontSize: Math.round(iconSize * 40),
          bold: true,
          align: "center",
          valign: "middle",
          color: palette.accent,
          fontFace: type.headerFont,
          margin: 0,
        });
        slide.addText(row.header, {
          x: MARGINS.left + iconSize + 0.3,
          y,
          w: usableW - iconSize - 0.3,
          h: rowH * 0.45,
          fontSize: type.headerSize - 2,
          bold: true,
          fontFace: type.headerFont,
          color: palette.textPrimary,
          margin: 0,
        });
        slide.addText(row.body, {
          x: MARGINS.left + iconSize + 0.3,
          y: y + rowH * 0.45,
          w: usableW - iconSize - 0.3,
          h: rowH * 0.55,
          fontSize: type.bodySize - 2,
          fontFace: type.bodyFont,
          color: palette.textMuted,
          margin: 0,
        });
      });
    });
  }

  timeline(options: TimelineOptions): this {
    return this.contentSlide(options.title, (slide) => {
      const palette = this.theme.palette;
      const type = this.theme.type;
      const n = Math.max(1, Math.min(options.steps.length, 6));
      const usableW = this.dim.width - MARGINS.left - MARGINS.right;
      const gap = 0.25;
      const stepW = (usableW - gap * (n - 1)) / n;
      const stepY = BODY_Y + 0.4;
      const lineY = stepY + 0.25;

      // Connecting line
      slide.addShape(this.pres.shapes.LINE, {
        x: MARGINS.left + stepW / 2,
        y: lineY,
        w: usableW - stepW,
        h: 0,
        line: { color: palette.primary, width: 2 },
      });

      options.steps.slice(0, n).forEach((step, i) => {
        const x = MARGINS.left + i * (stepW + gap);
        const cx = x + stepW / 2;
        slide.addShape(this.pres.shapes.OVAL, {
          x: cx - 0.25,
          y: lineY - 0.25,
          w: 0.5,
          h: 0.5,
          fill: { color: palette.primary },
          line: { color: palette.background, width: 2 },
        });
        slide.addText(String(i + 1), {
          x: cx - 0.25,
          y: lineY - 0.25,
          w: 0.5,
          h: 0.5,
          fontSize: 16,
          bold: true,
          align: "center",
          valign: "middle",
          color: palette.accent,
          margin: 0,
        });
        slide.addText(step.label, {
          x,
          y: lineY + 0.4,
          w: stepW,
          h: 0.4,
          fontSize: type.headerSize - 4,
          bold: true,
          align: "center",
          fontFace: type.headerFont,
          color: palette.textPrimary,
          margin: 0,
        });
        if (step.description) {
          slide.addText(step.description, {
            x,
            y: lineY + 0.85,
            w: stepW,
            h: 0.8,
            fontSize: type.bodySize - 3,
            align: "center",
            fontFace: type.bodyFont,
            color: palette.textMuted,
            margin: 0,
          });
        }
      });
    });
  }

  sectionDivider(options: SectionDividerOptions): this {
    const slide = this.pres.addSlide();
    slide.background = { color: this.theme.palette.primary };
    const palette = this.theme.palette;
    const type = this.theme.type;
    if (options.eyebrow) {
      slide.addText(options.eyebrow.toUpperCase(), {
        x: MARGINS.left,
        y: this.dim.height * 0.4,
        w: this.dim.width - MARGINS.left - MARGINS.right,
        h: 0.4,
        fontSize: 14,
        charSpacing: 4,
        bold: true,
        color: palette.accent,
        fontFace: type.headerFont,
        margin: 0,
      });
    }
    slide.addText(options.title, {
      x: MARGINS.left,
      y: this.dim.height * 0.5,
      w: this.dim.width - MARGINS.left - MARGINS.right,
      h: 1.2,
      fontSize: Math.max(52, type.titleSize + 12),
      bold: true,
      color: palette.accent,
      fontFace: type.headerFont,
      margin: 0,
    });
    if (options.subtitle) {
      slide.addText(options.subtitle, {
        x: MARGINS.left,
        y: this.dim.height * 0.7,
        w: this.dim.width - MARGINS.left - MARGINS.right,
        h: 0.6,
        fontSize: type.headerSize,
        color: palette.secondary,
        fontFace: type.bodyFont,
        margin: 0,
      });
    }
    this.slideIndex += 1;
    return this;
  }

  closing(options: ClosingOptions): this {
    const slide = this.pres.addSlide();
    slide.background = { color: this.theme.palette.primary };
    const palette = this.theme.palette;
    const type = this.theme.type;
    slide.addText(options.title, {
      x: MARGINS.left,
      y: this.dim.height * 0.35,
      w: this.dim.width - MARGINS.left - MARGINS.right,
      h: 1.4,
      fontSize: Math.max(52, type.titleSize + 12),
      bold: true,
      color: palette.accent,
      fontFace: type.headerFont,
      align: "center",
      margin: 0,
    });
    if (options.subtitle) {
      slide.addText(options.subtitle, {
        x: MARGINS.left,
        y: this.dim.height * 0.55,
        w: this.dim.width - MARGINS.left - MARGINS.right,
        h: 0.6,
        fontSize: type.headerSize,
        color: palette.secondary,
        fontFace: type.bodyFont,
        align: "center",
        margin: 0,
      });
    }
    if (options.cta) {
      slide.addText(options.cta, {
        x: MARGINS.left,
        y: this.dim.height * 0.7,
        w: this.dim.width - MARGINS.left - MARGINS.right,
        h: 0.5,
        fontSize: type.bodySize,
        color: palette.secondary,
        fontFace: type.bodyFont,
        align: "center",
        margin: 0,
      });
    }
    this.slideIndex += 1;
    return this;
  }

  // Escape hatch: grab the raw pptxgenjs slide if the caller needs something
  // the primitives don't cover yet. The caller is responsible for layout.
  rawSlide(): pptxgen.Slide {
    const slide = this.pres.addSlide();
    slide.background = { color: this.theme.palette.background };
    this.slideIndex += 1;
    return slide;
  }

  async save(outPath: string): Promise<void> {
    await this.pres.writeFile({ fileName: outPath });
  }

  // -------------------------------------------------------------------------
  // Internals
  // -------------------------------------------------------------------------

  private drawChrome(slide: pptxgen.Slide, title: string): void {
    const palette = this.theme.palette;
    const type = this.theme.type;

    if (this.opts.showAccentBand) {
      slide.addShape(this.pres.shapes.RECTANGLE, {
        x: 0,
        y: 0,
        w: this.dim.width,
        h: ACCENT_BAND_HEIGHT,
        fill: { color: palette.primary },
        line: { type: "none" },
      });
    }

    slide.addText(title, {
      x: MARGINS.left,
      y: TITLE_Y,
      w: this.dim.width - MARGINS.left - MARGINS.right,
      h: TITLE_HEIGHT,
      fontSize: type.titleSize,
      bold: true,
      color: palette.primary,
      fontFace: type.headerFont,
      margin: 0,
    });

    if (this.opts.showFooter) {
      const footerY = this.dim.height - 0.35;
      const left = this.opts.company || this.opts.title || "";
      if (left) {
        slide.addText(left, {
          x: MARGINS.left,
          y: footerY,
          w: this.dim.width / 2,
          h: 0.3,
          fontSize: type.captionSize,
          color: palette.textMuted,
          fontFace: type.bodyFont,
          margin: 0,
        });
      }
      slide.addText(`${this.slideIndex + 1}`, {
        x: this.dim.width - MARGINS.right - 0.5,
        y: footerY,
        w: 0.5,
        h: 0.3,
        fontSize: type.captionSize,
        color: palette.textMuted,
        fontFace: type.bodyFont,
        align: "right",
        margin: 0,
      });
    }
  }

  private renderColumn(
    slide: pptxgen.Slide,
    content: ColumnContent,
    x: number,
    y: number,
    w: number,
    h: number,
  ): void {
    const palette = this.theme.palette;
    const type = this.theme.type;

    if ("bullets" in content) {
      slide.addText(
        content.bullets.map((t, i) => ({
          text: t,
          options: {
            bullet: true,
            breakLine: i !== content.bullets.length - 1,
          },
        })),
        {
          x,
          y,
          w,
          h,
          fontSize: type.bodySize,
          fontFace: type.bodyFont,
          color: palette.textPrimary,
          paraSpaceAfter: 8,
          valign: "top",
        },
      );
      return;
    }
    if ("paragraph" in content) {
      slide.addText(content.paragraph, {
        x,
        y,
        w,
        h,
        fontSize: type.bodySize,
        fontFace: type.bodyFont,
        color: palette.textPrimary,
        valign: "top",
      });
      return;
    }
    if ("imagePath" in content || "imageData" in content) {
      const opts: Record<string, unknown> = {
        x,
        y,
        w,
        h,
        sizing: { type: "contain", w, h },
      };
      if (content.imagePath) opts.path = content.imagePath;
      if (content.imageData) opts.data = content.imageData;
      slide.addImage(opts as pptxgen.ImageProps);
      if (content.caption) {
        slide.addText(content.caption, {
          x,
          y: y + h - 0.3,
          w,
          h: 0.3,
          fontSize: type.captionSize,
          color: palette.textMuted,
          align: "center",
          italic: true,
          fontFace: type.bodyFont,
          margin: 0,
        });
      }
      return;
    }
    if ("chart" in content) {
      const chart = content.chart;
      const map = {
        bar: this.pres.charts.BAR,
        line: this.pres.charts.LINE,
        pie: this.pres.charts.PIE,
        doughnut: this.pres.charts.DOUGHNUT,
      } as const;
      const data = chart.series.map((s) => ({
        name: s.name,
        labels: chart.categories,
        values: s.values,
      }));
      slide.addChart(map[chart.type], data as pptxgen.ChartData, {
        x,
        y,
        w,
        h,
        barDir: "col",
        chartColors: [...palette.chart],
        catAxisLabelColor: palette.textMuted,
        valAxisLabelColor: palette.textMuted,
        catAxisLabelFontSize: 10,
        valAxisLabelFontSize: 10,
        valGridLine: { color: palette.surface, size: 0.5, style: "solid" },
        catGridLine: { style: "none" },
        showLegend: chart.series.length > 1,
        legendPos: "b",
        legendColor: palette.textMuted,
        legendFontSize: 10,
        chartArea: {
          fill: { color: palette.background },
          roundedCorners: true,
        },
      });
      return;
    }
    throw new Error("Unsupported column content type");
  }
}
