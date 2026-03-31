import * as fs from "fs";
import * as path from "path";
import dotenv from "dotenv";

export function loadEnvFiles(): void {
  const candidates = [
    path.join(process.cwd(), ".env"),
    path.join(__dirname, "..", "..", ".env"),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) {
      dotenv.config({ path: p });
      break;
    }
  }
}
