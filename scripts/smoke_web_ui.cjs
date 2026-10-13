// Optional browser QA: provide a PDF and a running, configured NER server.
const { chromium } = require(process.env.NER_PLAYWRIGHT_MODULE || "playwright");
const fs = require("node:fs");
const assert = require("node:assert/strict");
const base = process.env.NER_UI_URL || "http://127.0.0.1:8000";
const pdf = process.argv[2];
if (!pdf || !fs.existsSync(pdf))
  throw new Error("Usage: node scripts/smoke_web_ui.cjs /path/to/sample.pdf");

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox"],
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1280, height: 960 },
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(base);
    await page.waitForFunction(
      () => !document.querySelector("#backend").disabled,
    );
    await page.screenshot({ path: "/tmp/ner-ui-desktop.png", fullPage: true });
    await page.locator("#pdf-file").setInputFiles({
      name: "empty.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.alloc(0),
    });
    await page.locator("#form-error").waitFor({ state: "visible" });
    assert.match(await page.locator("#form-error").innerText(), /空/);
    await page.locator("#pdf-file").setInputFiles(pdf);
    const accepted = page.waitForResponse(
      (r) => r.url().endsWith("/api/jobs") && r.request().method() === "POST",
    );
    await page.locator("#start-button").click();
    const receipt = await accepted;
    assert.equal(receipt.status(), 202);
    const job = await receipt.json();
    await page
      .locator("#results")
      .waitFor({ state: "visible", timeout: 120000 });
    await page.waitForFunction(() => {
      const image = document.querySelector("#translated-frame");
      return image.complete && image.naturalWidth > 0;
    });
    assert.equal(
      await page.locator("#status-title").innerText(),
      "翻訳が完了しました",
    );
    const result = await (
      await page.request.get(`${base}/api/jobs/${job.id}`)
    ).json();
    const download = await page.request.get(base + result.downloads.translated);
    assert.equal(download.status(), 200);
    await page.screenshot({ path: "/tmp/ner-ui-complete.png", fullPage: true });
    await page.reload();
    await page
      .locator("#results")
      .waitFor({ state: "visible", timeout: 15000 });
    assert.equal(
      await page.locator("#status-filename").innerText(),
      job.filename,
    );
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: "/tmp/ner-ui-mobile.png", fullPage: true });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
    );
    assert.deepEqual(errors, []);
    console.log(
      JSON.stringify({
        job: job.id,
        downloadBytes: (await download.body()).length,
        reloadRecovered: true,
        mobileOverflow: false,
        pageErrors: errors,
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
