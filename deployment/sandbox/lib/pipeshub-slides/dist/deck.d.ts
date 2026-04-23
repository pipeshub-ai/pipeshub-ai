type pptxgen = any;
declare namespace pptxgen {
    type Slide = any;
    type ImageProps = any;
    type ChartData = any;
}
import { LayoutName, LayoutDimensions } from "./layout.js";
import { Theme, ThemeName } from "./themes.js";
export interface DeckOptions {
    theme: Theme | ThemeName | string;
    layout?: LayoutName;
    title?: string;
    author?: string;
    company?: string;
    showAccentBand?: boolean;
    showFooter?: boolean;
}
export interface TitleSlideOptions {
    title: string;
    subtitle?: string;
    eyebrow?: string;
    author?: string;
    date?: string;
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
    imageData?: string;
    caption?: string;
}
export interface ChartContent {
    chart: {
        type: "bar" | "line" | "pie" | "doughnut";
        categories: string[];
        series: {
            name: string;
            values: number[];
        }[];
    };
}
export type ColumnContent = BulletContent | ParagraphContent | ImageContent | ChartContent;
export interface TwoColumnOptions {
    title: string;
    left: ColumnContent;
    right: ColumnContent;
    split?: 50 | 60 | 40;
}
export interface Stat {
    value: string;
    label: string;
}
export interface StatGridOptions {
    title: string;
    stats: Stat[];
}
export interface IconRow {
    icon?: string;
    header: string;
    body: string;
}
export interface IconRowsOptions {
    title: string;
    rows: IconRow[];
}
export interface TimelineStep {
    label: string;
    description?: string;
}
export interface TimelineOptions {
    title: string;
    steps: TimelineStep[];
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
export declare class Deck {
    readonly pres: any;
    readonly theme: Theme;
    readonly dim: LayoutDimensions;
    private readonly opts;
    private slideIndex;
    constructor(options: DeckOptions);
    titleSlide(options: TitleSlideOptions): this;
    contentSlide(title: string, render: (slide: pptxgen.Slide) => void): this;
    twoColumn(options: TwoColumnOptions): this;
    statGrid(options: StatGridOptions): this;
    iconRows(options: IconRowsOptions): this;
    timeline(options: TimelineOptions): this;
    sectionDivider(options: SectionDividerOptions): this;
    closing(options: ClosingOptions): this;
    rawSlide(): pptxgen.Slide;
    save(outPath: string): Promise<void>;
    private drawChrome;
    private renderColumn;
}
export {};
