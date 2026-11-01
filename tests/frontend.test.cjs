// Development-only browser checks. Every archive is synthetic; no Chrome profile is read.
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs/promises");
const { existsSync } = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { before, after, test } = require("node:test");
const { chromium } = require("playwright-core");

const root = path.resolve(__dirname, "..");
const python = process.env.PYTHON || "python3";
let browser, temporary, demo;

function chromePath() {
  if (process.env.CHROME_BIN) return path.resolve(process.env.CHROME_BIN);
  for (const name of ["google-chrome", "chromium", "chromium-browser"]) {
    for (const directory of (process.env.PATH || "").split(path.delimiter)) {
      const candidate = path.join(directory, name);
      if (existsSync(candidate)) return candidate;
    }
  }
  throw new Error("Set CHROME_BIN to a Chrome/Chromium executable path.");
}

before(async () => {
  temporary = await fs.mkdtemp(path.join(os.tmpdir(), "tab-capsule-browser-"));
  const output = path.join(temporary, "generated");
  execFileSync(python, ["demo.py", "--output-dir", output], { cwd: root });
  // Move just the HTML, then remove its original directory: no server or sidecars.
  demo = path.join(temporary, "standalone.html");
  await fs.rename(path.join(output, "index.html"), demo);
  await fs.rm(output, { recursive: true });
  browser = await chromium.launch({
    executablePath: chromePath(),
    headless: true,
  });
});

after(async () => {
  await browser?.close();
  if (temporary) await fs.rm(temporary, { recursive: true, force: true });
});

async function openArchive(t, file = demo) {
  const context = await browser.newContext({
    locale: "en-GB",
    timezoneId: "Europe/Prague",
  });
  const errors = [],
    requests = [];
  // Do not allow a regression to make an external request, including favicon loads.
  await context.route(/https?:\/\//, async (route) => {
    requests.push(route.request().url());
    await route.abort();
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  t.after(async () => {
    await context.close();
    assert.deepEqual(errors, [], "no JavaScript or browser console errors");
    assert.deepEqual(requests, [], "no external requests");
  });
  await page.goto(pathToFileURL(file).href);
  await page.waitForFunction(() =>
    document.querySelector("#gen").textContent.startsWith("updated "),
  );
  return page;
}

async function query(page, value) {
  await page.locator("#q").fill(value);
  // Wait for the existing 200 ms debounce and its visible result.
  await page.waitForFunction(
    (q) => document.querySelector("h2")?.textContent === "Search: " + q.trim(),
    value,
  );
}

async function fixture(name, data) {
  const html = execFileSync(
    python,
    [
      "-c",
      "import json, sys; from tab_capsule.render import render_html; sys.stdout.write(render_html(json.load(sys.stdin)))",
    ],
    { cwd: root, input: JSON.stringify(data), encoding: "utf8" },
  );
  const file = path.join(temporary, name + ".html");
  await fs.writeFile(file, html);
  return file;
}

test("standalone demo loads styles, snapshots, history and day navigation", async (t) => {
  const page = await openArchive(t);
  assert.equal(await page.title(), "Tab Capsule");
  assert.equal(await page.locator("#days a").count(), 3);
  assert.equal(
    await page.locator("#days a.sel").getAttribute("href"),
    "#2026-09-03",
  );
  assert.match(
    await page.locator("#main > details > summary").first().innerText(),
    /Demo.*4 tabs · 1 windows · 1 new/,
  );
  assert.equal(await page.locator(".pin").count(), 1);
  assert.equal(await page.locator(".badge").innerText(), "NEW");
  assert.equal(await page.locator("details.hr li").count(), 4);
  assert.equal(
    await page
      .locator("header")
      .evaluate((element) => getComputedStyle(element).position),
    "sticky",
  );
  await page.getByRole("button", { name: "expand all", exact: true }).click();
  assert.equal(await page.locator("details.hr:not([open])").count(), 0);
  await page.getByRole("button", { name: "collapse all", exact: true }).click();
  assert.equal(await page.locator("details.hr[open]").count(), 0);
  await page.locator('#days a[href="#2026-09-01"]').click();
  await page.waitForFunction(
    () => document.querySelector("#days a.sel")?.hash === "#2026-09-01",
  );
  assert.match(
    await page.locator("#main > details > summary").first().innerText(),
    /2 tabs/,
  );
  assert.equal(await page.locator(".badge").count(), 0);
});

test("literal search combines URL/title terms, keeps recency and deduplicates tabs", async (t) => {
  const page = await openArchive(t);
  await query(page, "  EXAMPLE.ORG   shader  ");
  assert.equal(
    await page.locator("#main > .sub").innerText(),
    "3 visits · 1 open-tab matches",
  );
  assert.equal(
    await page.locator("#main > details").first().locator("li").count(),
    1,
  );
  assert.equal(
    await page.locator("#main > details").first().locator(".jump").innerText(),
    "2026-09-03",
  );
  assert.deepEqual(
    await page
      .locator("#main > details")
      .last()
      .locator(".jump")
      .allTextContents(),
    ["2026-09-03", "2026-09-02", "2026-09-01"],
  );
  await query(page, "shaeder");
  assert.equal(
    await page.locator("#main > .sub").innerText(),
    "0 visits · 0 open-tab matches",
  );
  assert.equal(await page.locator(".empty").innerText(), "No visits match.");
  await query(page, "grid");
  await page.locator("#main > details").last().locator(".jump").last().click();
  assert.equal(await page.locator("#q").inputValue(), "");
  assert.equal(
    await page.locator("#days a.sel").getAttribute("href"),
    "#2026-09-01",
  );
});

test("keyboard shortcuts, short queries and hash fallback keep day routing", async (t) => {
  const page = await openArchive(t);
  await page.keyboard.press("/");
  assert.equal(
    await page
      .locator("#q")
      .evaluate((element) => element === document.activeElement),
    true,
  );
  await query(page, "postgres");
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("#q").inputValue(), "");
  assert.equal(
    await page.locator("#days a.sel").getAttribute("href"),
    "#2026-09-03",
  );
  await page.locator("#q").fill("x");
  await page.waitForTimeout(250);
  assert.equal(
    await page.locator("#main h2").innerText(),
    "Thursday, 3 September 2026",
  );
  await page.locator("#q").fill("");
  await page.evaluate(() => {
    location.hash = "missing-day";
  });
  await page.waitForTimeout(250);
  assert.equal(
    await page.locator("#days a.sel").getAttribute("href"),
    "#2026-09-03",
  );
});

test("compressed Unicode/HTML titles stay text and unsafe links stay blocked", async (t) => {
  const title =
    'Příliš 🐴 </script><img src="https://example.org/probe"> & "quoted"';
  const file = await fixture("escaping", {
    S: ["javascript:alert(1)", title],
    P: ["Synthetic <profile>"],
    V: [[1788436800, 0, 1, 0, 1]],
    D: [["2026-09-03", 0, 1]],
    T: [
      {
        p: 0,
        d: "2026-09-03",
        t: 1788436800,
        s: 1788436800,
        w: [[[0, 1, 0, 0]]],
      },
    ],
    generated: 1788436800,
  });
  const page = await openArchive(t, file);
  await query(page, "PŘÍLIŠ 🐴");
  assert.equal(
    await page.locator("#main > .sub").innerText(),
    "1 visits · 1 open-tab matches",
  );
  assert.deepEqual(await page.locator(".ti a").allTextContents(), [
    title,
    title,
  ]);
  assert.equal(await page.locator("#main img, #main script").count(), 0);
  for (const link of await page.locator(".ti a").all()) {
    assert.equal(await link.getAttribute("href"), "#");
    assert.equal(await link.getAttribute("rel"), "noreferrer noopener");
  }
});

test("existing search display limits preserve total counts", async (t) => {
  const data = {
    S: [],
    P: ["Synthetic"],
    V: [],
    D: [["2026-09-03", 0, 305]],
    T: [],
    generated: 1788436800,
  };
  const tabs = [];
  for (let i = 0; i < 105; i++) {
    data.S.push("https://example.org/" + i, "Needle " + i);
    tabs.push([2 * i, 2 * i + 1, 0, 0]);
  }
  for (let i = 0; i < 305; i++) data.V.push([1788436800 + i, 0, 1, 0, 1]);
  data.T.push({
    p: 0,
    d: "2026-09-03",
    t: 1788436800,
    s: 1788436800,
    w: [tabs],
  });
  const page = await openArchive(t, await fixture("limits", data));
  await query(page, "needle");
  assert.equal(
    await page.locator("#main > .sub").innerText(),
    "305 visits (showing newest 300) · 105 open-tab matches",
  );
  assert.equal(
    await page.locator("#main > details").first().locator("li").count(),
    100,
  );
  assert.equal(
    await page.locator("#main > details").last().locator("li").count(),
    300,
  );
});

test("empty archive loads and can be searched", async (t) => {
  const page = await openArchive(
    t,
    await fixture("empty", {
      S: [],
      P: [],
      V: [],
      D: [],
      T: [],
      generated: 1788436800,
    }),
  );
  assert.equal(await page.locator("#main").innerText(), "No data yet.");
  await query(page, "example");
  assert.equal(
    await page.locator("#main > .sub").innerText(),
    "0 visits · 0 open-tab matches",
  );
});
