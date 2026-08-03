import readline from "node:readline";
import { chromium } from "playwright";

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ acceptDownloads: true });
const page = await context.newPage();

function respond(id, payload) {
  process.stdout.write(`${JSON.stringify({ id, ...payload })}\n`);
}

async function handle(request) {
  const { id, operation, arguments: args = {} } = request;
  try {
    let result;
    if (operation === "navigate") {
      await page.goto(String(args.url), { waitUntil: "domcontentloaded" });
      result = await page.title();
    } else if (operation === "read") {
      const locator = page.locator(String(args.selector ?? "body"));
      result = (await locator.innerText()).slice(0, Number(args.max_chars ?? 8000));
    } else if (operation === "submit") {
      const form = page.locator(String(args.selector));
      for (const [name, value] of Object.entries(args.fields ?? {})) {
        await form.locator(`[name=${JSON.stringify(name)}]`).fill(String(value));
      }
      await Promise.all([
        page.waitForLoadState("domcontentloaded").catch(() => undefined),
        form.evaluate((node) => node.requestSubmit()),
      ]);
      result = page.url();
    } else if (operation === "download") {
      const [download] = await Promise.all([
        page.waitForEvent("download"),
        page.locator(String(args.selector)).click(),
      ]);
      await download.saveAs(String(args.target));
      result = String(args.target);
    } else if (operation === "upload") {
      await page.locator(String(args.selector)).setInputFiles(String(args.source));
      result = String(args.source);
    } else if (operation === "close") {
      await browser.close();
      respond(id, { ok: true, result: "closed" });
      process.exit(0);
    } else {
      throw new Error(`Unbekannte Browseroperation: ${operation}`);
    }
    respond(id, { ok: true, result });
  } catch (error) {
    respond(id, { ok: false, error: String(error?.stack ?? error) });
  }
}

const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of lines) {
  if (!line.trim()) continue;
  let request;
  try {
    request = JSON.parse(line);
  } catch (error) {
    respond(null, { ok: false, error: `Ungültiges JSON: ${error}` });
    continue;
  }
  await handle(request);
}

await browser.close();
