import { DocTheme, DocThemeName } from "./themes.js";
export type PaperSize = "letter" | "a4";
export interface ReportOptions {
    theme: DocTheme | DocThemeName | string;
    title?: string;
    subtitle?: string;
    author?: string;
    paperSize?: PaperSize;
    orientation?: "portrait" | "landscape";
    marginInches?: number;
    headerText?: string;
    showFooterPageNumbers?: boolean;
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
    columnWidths?: number[];
}
export interface ImageOptions {
    path: string;
    widthInches: number;
    heightInches: number;
    caption?: string;
}
export declare class Report {
    readonly theme: DocTheme;
    readonly paper: {
        width: number;
        height: number;
    };
    readonly marginDxa: number;
    readonly contentWidthDxa: number;
    private readonly opts;
    private readonly children;
    constructor(options: ReportOptions);
    coverPage(options: CoverPageOptions): this;
    tableOfContents(): this;
    heading1(text: string): this;
    heading2(text: string): this;
    heading3(text: string): this;
    paragraph(text: string): this;
    bulletList(items: string[]): this;
    numberedList(items: string[]): this;
    calloutBox(text: string): this;
    table(options: TableOptions): this;
    image(options: ImageOptions): this;
    pageBreak(): this;
    save(outPath: string): Promise<void>;
    private buildDocument;
}
