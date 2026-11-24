// `pipeshub-native <command>`: run by the bundle's Node (24.12 or later runs TypeScript directly).
import { run } from "./cli.ts";

process.exit(await run(process.argv.slice(2), [process.execPath, import.meta.filename]));
