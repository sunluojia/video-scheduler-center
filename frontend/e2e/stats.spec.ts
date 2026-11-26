/// <reference lib="dom" />
import { expect, type Page, test } from "@playwright/test";
import {
  crawlIpHistory,
  crawlIpStats,
  historyBatch,
  runningBatch,
} from "../src/pages/stats/crawl-ips.fixtures";
import { mockApi } from "./mock";

const HOUR = 3600_000;
const start = Math.floor(Date.now() / HOUR) * HOUR - 23 * HOUR;
const counts = {
  profile_ok: 10,
  profile_failed: 0,
  listing_ok: 9,
  listing_failed: 1,
  unreachable: 1,
  videos_found: 80,
  downloaded: 40,
  downloaded_bytes: 2 * 1024 ** 3,
  download_failed: 40,
  analyzed: 38,
  reused: 4,
  analysis_failed: 2,
  summarized: 8,
  summary_failed: 0,
};
const timeline = Array.from({ length: 24 }, (_, index) => ({
  ...Object.fromEntries(
    Object.entries(counts).map(([key, value]) => [key, index % 3 ? 0 : Math.round(value / 8)]),
  ),
  at: new Date(start + index * HOUR).toISOString(),
}));
const stats = {
  since: timeline[0].at,
  until: new Date(start + 24 * HOUR).toISOString(),
  bucket: "hour",
  batch_id: null,
  totals: { ...counts, profile_ok: 80, listing_ok: 72, listing_failed: 8, unreachable: 8 },
  conclusions: { ok: 72, private: 5, login_required: 3, failed: 8 },
  timeline,
  stages: [
    { stage: "fetch", succeeded: 40, failed: 0, retried: 0, skipped: 0 },
    { stage: "recognize", succeeded: 38, failed: 2, retried: 1, skipped: 0 },
  ],
  durations: [{ stage: "crawl", count: 80, average: 42, median: 35, p90: 90 }],
  failures: [
    { stage: "download", reason: "连接超时", count: 30 },
    { stage: "crawl", reason: "作品列表等待响应超时", count: 8 },
  ],
  creators: { completed: 60, downloading: 12, recrawl: 8 },
  videos: { completed: 300, analyzing: 20, failed: 2 },
};
const usage = [
  {
    at: timeline[0].at,
    kind: "recognize",
    model_id: "m1",
    model: "qwen3.6-plus",
    calls: 40,
    succeeded: 38,
    failed: 2,
    input_tokens: 400_000,
    output_tokens: 20_000,
    cached_tokens: 300_000,
    cache_input_tokens: 400_000,
    busy_seconds: 7200,
  },
];

test("数据看板显示各环节、问题提示、负载和导出", async ({ page }) => {
  await mockApi(page, {
    "GET /api/batches": { items: [], total: 0 },
    "GET /api/stats": stats,
    "GET /api/stats/crawl-ips": crawlIpStats,
    "GET /api/stats/crawl-ip-batches": { items: [runningBatch], total: 1 },
    "GET /api/ai/usage": usage,
    "GET /api/ai/models": [{ id: "m1", name: "识别模型", max_concurrency: 4 }],
    "GET /api/executors": [
      { kind: "crawler", instance_id: "c1", online: true, crawl_slots: 4, download_slots: 6 },
    ],
    "GET /api/executors/crawler/crawl-progress": [
      {
        instance_id: "c1",
        crawls: [
          {
            task_id: "t1",
            handle: "alice",
            stage: "listing",
            pages_read: 1,
            works_found: 3,
            max_works: 10,
          },
          {
            task_id: "t2",
            handle: "bob",
            stage: "waiting_line",
            pages_read: 0,
            works_found: 0,
            max_works: 10,
          },
        ],
      },
    ],
    "GET /api/executors/crawler/download-progress": [
      { instance_id: "c1", downloads: [], download_bytes_per_second: null },
    ],
    "GET /api/executors/analysis/status": {
      instance_id: "a1",
      version: "1",
      jobs: {},
      ready: { recognize: 12 },
      running: { recognize: 3 },
      models: [
        { model_id: "m1", running: 3, capacity: 4, recent_requests: 20, requests_per_minute: 60 },
      ],
      free_disk_bytes: 0,
    },
  });
  await page.goto("/stats");

  await expect(page.getByRole("heading", { name: "数据看板" })).toBeVisible();
  await expect(
    page.getByText("视频下载成功率 50.0%（失败 40），最多的原因：连接超时（30 次）"),
  ).toBeVisible();
  await expect(page.getByText("8 位达人需要重新抓取，可在批次里点“重新抓取”")).toBeVisible();
  await expect(page.getByText("命中 30.0万 / 输入 40.0万 token")).toBeVisible();
  await expect(page.getByText("其中 1 个在等代理线路")).toBeVisible();
  await expect(page.getByText("分析排队：识别 12")).toBeVisible();

  const exported = page.getByRole("link", { name: "导出 Excel" });
  await expect(exported).toHaveAttribute("href", /\/api\/stats\/export\?since=.+&bucket=hour/);

  await page.getByRole("group", { name: "图表类型" }).first().getByText("表格").click();
  await expect(page.getByRole("columnheader", { name: "达人基础信息" })).toBeVisible();

  await page.getByRole("button", { name: "近 7 天" }).click();
  await expect(page.getByRole("button", { name: "按天" })).toHaveAttribute("aria-pressed", "true");
  await expect(page).toHaveURL(/range=week/);
});

async function mockCrawlIps(page: Page, withHistory = false) {
  const total = withHistory ? 51 : 1;
  await mockApi(page, {
    "GET /api/batches": { items: [], total: 0 },
    "GET /api/stats": stats,
    "GET /api/stats/crawl-ips": crawlIpStats,
    "GET /api/stats/crawl-ip-batches": { items: [runningBatch], total },
    "GET /api/ai/usage": usage,
    "GET /api/ai/models": [],
    "GET /api/executors": [],
  });
  await page.route(
    (url) => url.pathname === "/api/stats/crawl-ip-batches" && url.searchParams.get("page") === "2",
    (route) => route.fulfill({ json: { items: [historyBatch], total } }),
  );
  await page.route(
    (url) =>
      url.pathname === "/api/stats/crawl-ips" &&
      url.searchParams.get("batch_id") === historyBatch.id,
    (route) => route.fulfill({ json: crawlIpHistory }),
  );
}

test("任务 IP 看板比较各线路并切换到后页的历史批次", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await mockCrawlIps(page, true);
  await page.goto("/stats");

  const panel = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "任务各 IP 采集与风控看板" }) });
  const picker = panel.getByRole("combobox", { name: "采集批次" });
  await expect(picker).toContainText(runningBatch.name);
  await expect(panel.getByText("19.05%")).toBeVisible();
  const detail = panel.getByRole("table", { name: "采集 IP 明细" });
  await expect(detail.getByRole("row")).toHaveCount(4);
  const riskLine = detail.getByRole("row", { name: /直连美国家宽 01/ });
  await expect(riskLine.getByText("偏高")).toBeVisible();
  await expect(riskLine.getByText("退避中")).toBeVisible();
  await expect(riskLine.getByText("空正文 2")).toBeVisible();

  await panel.getByRole("button", { name: "采集占比" }).click();
  await expect(panel.getByRole("list", { name: "采集占比对比图" }).getByText("60%")).toBeVisible();
  await panel.getByRole("button", { name: "风控率" }).click();
  await expect(panel.getByRole("list", { name: "风控率对比图" }).getByText("30%")).toBeVisible();
  await panel.screenshot({
    path: testInfo.outputPath("crawl-ips-desktop.png"),
    animations: "disabled",
  });

  await panel.getByRole("button", { name: "下一页" }).click();
  await picker.press("Enter");
  await page.getByRole("option", { name: /上周的任务/ }).click();
  await expect(panel.getByText(/该历史批次暂无可用的采集租约明细/)).toBeVisible();
  await expect(detail).toHaveCount(0);
  await expect(picker).toContainText(historyBatch.name);
  await picker.click();
  await page.getByRole("option", { name: "自动选择最近批次" }).click();
  await expect(detail).toBeVisible();
  await expect(picker).toContainText(runningBatch.name);
  await expect(page.getByRole("link", { name: "导出 Excel" })).toHaveAttribute(
    "href",
    /\/api\/stats\/export/,
  );
});

test("窄屏 IP 看板可横向滚动明细并保留页面宽度", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockCrawlIps(page);
  await page.goto("/stats");

  const panel = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "任务各 IP 采集与风控看板" }) });
  const region = panel.getByRole("region", { name: "采集 IP 明细滚动区域" });
  await expect(panel.getByRole("combobox", { name: "采集批次" })).toBeVisible();
  await expect(region).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
    .toBe(true);
  expect(await region.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
  await panel.screenshot({
    path: testInfo.outputPath("crawl-ips-mobile.png"),
    animations: "disabled",
  });
  const lineHeader = region.getByRole("rowheader", { name: /直连美国家宽 01/ });
  const lineBefore = await lineHeader.boundingBox();
  await region.focus();
  await page.keyboard.press("ArrowRight");
  await expect.poll(() => region.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
  expect((await lineHeader.boundingBox())?.x).toBe(lineBefore?.x);
  await expect(page.getByRole("navigation").getByRole("link", { name: "数据看板" })).toBeVisible();

  await page.setViewportSize({ width: 320, height: 740 });
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
    .toBe(true);
});
