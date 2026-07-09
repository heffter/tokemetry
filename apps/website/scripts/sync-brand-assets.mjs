import { copyFile, mkdir, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const websiteRoot = resolve(here, "..");
const repoRoot = resolve(websiteRoot, "..", "..");
const source = resolve(repoRoot, "assets", "brand");
const publicRoot = resolve(websiteRoot, "public");
const brandOut = resolve(publicRoot, "brand");

const files = [
  "tokemetry-logo-horizontal-dark.svg",
  "tokemetry-logo-horizontal-light.svg",
  "tokemetry-logo-vertical-dark.svg",
  "tokemetry-logo-vertical-light.svg",
  "tokemetry-logo-monochrome-dark.svg",
  "tokemetry-icon-dark.svg",
  "tokemetry-icon-light.svg",
  "favicon.svg",
];

await rm(brandOut, { recursive: true, force: true });
await mkdir(brandOut, { recursive: true });

for (const file of files) {
  await copyFile(resolve(source, file), resolve(brandOut, file));
}

await copyFile(resolve(source, "favicon.svg"), resolve(publicRoot, "favicon.svg"));
