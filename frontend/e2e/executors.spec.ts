import { expect, test } from "@playwright/test";
import { mockApi } from "./mock";

test("登记执行程序后等待心跳，离线记录可删除", async ({ page }) => {
  const now = new Date().toISOString();
  const pendingUrl = "http://worker.example.test:8001";
  const crawler = {
    kind: "crawler",
    instance_id: "crawler-online",
    url: "http://127.0.0.1:8091",
    version: "1.0",
    online: true,
    registered_at: now,
    last_seen_at: now,
    withdrawn_at: null,
    crawl_slots: 2,
    download_slots: 4,
    warm_slots: 1,
    activity: { active: 3, completed_24h: 12, expired_24h: 1, takeovers_24h: 2 },
  };
  const existing = [crawler, { ...crawler, instance_id: "crawler-stopped", online: false }];
  await mockApi(page, {
    "GET /api/identities": { target: 8, devices: [], alerts: [] },
    "GET /api/proxy-alerts": [],
  });
  await page.route(
    (url) => url.pathname === "/api/executors",
    async (route) => {
      if (route.request().method() === "GET") return route.fulfill({ json: existing });
      const body = route.request().postDataJSON();
      expect(body).toEqual({ kind: "analysis", instance_id: "analysis-next", url: pendingUrl });
      const pending = {
        ...crawler,
        ...body,
        online: false,
        last_seen_at: null,
        version: "",
      };
      existing.push(pending);
      return route.fulfill({ status: 201, json: pending });
    },
  );
  await page.route(
    (url) => url.pathname === "/api/executors/crawler/crawler-stopped",
    async (route) => {
      expect(route.request().method()).toBe("DELETE");
      existing.splice(1, 1);
      return route.fulfill({ status: 204 });
    },
  );

  await page.goto("/executors");
  const online = page.getByRole("row", { name: /crawler-online/ });
  await expect(online.getByText("在线", { exact: true })).toBeVisible();
  await expect(online.getByText("3 个认领")).toBeVisible();
  await expect(online.getByText("完成 12 · 过期 1 · 接手 2")).toBeVisible();
  await expect(online.getByRole("button", { name: "删除" })).toHaveCount(0);

  await page.getByRole("button", { name: "添加执行程序" }).click();
  const form = page.getByRole("dialog", { name: "添加执行程序" });
  await form.getByRole("combobox", { name: "类型" }).click();
  await page.getByRole("option", { name: "分析服务" }).click();
  await form.getByLabel("实例 ID").fill("analysis-next");
  await form.getByLabel("访问地址").fill(pendingUrl);
  await form.getByRole("button", { name: "添加", exact: true }).click();
  await expect(page.getByRole("row", { name: /analysis-next/ })).toContainText("待心跳");

  const stopped = page.getByRole("row", { name: /crawler-stopped/ });
  await stopped.getByRole("button", { name: "删除" }).click();
  const confirmation = page.getByRole("alertdialog", { name: "删除执行程序" });
  await expect(confirmation).toContainText("crawler-stopped");
  await confirmation.getByRole("button", { name: "删除", exact: true }).click();
  await expect(stopped).toHaveCount(0);
  await expect(online).toBeVisible();
});

test("保存并重新打开全为 0 的爬取程序并发", async ({ page }) => {
  const now = new Date().toISOString();
  const crawler = {
    kind: "crawler",
    instance_id: "crawler-1",
    url: "http://127.0.0.1:8091",
    version: "1.0",
    online: true,
    registered_at: now,
    last_seen_at: now,
    withdrawn_at: null,
    crawl_slots: 2,
    download_slots: 4,
    warm_slots: 1,
  };
  const executors = [crawler];
  await mockApi(page, {
    "GET /api/executors": executors,
    "GET /api/identities": { target: 8, devices: [], alerts: [] },
    "GET /api/proxy-alerts": [],
  });
  await page.route("**/api/executors/crawler/crawler-1/slots", async (route) => {
    expect(route.request().method()).toBe("PUT");
    const slots = route.request().postDataJSON();
    expect(slots).toEqual({ crawl_slots: 0, download_slots: 0, warm_slots: 0 });
    executors[0] = { ...crawler, ...slots };
    await route.fulfill({ json: executors[0] });
  });
  await page.goto("/executors");

  await page.getByRole("button", { name: "采集 2 · 下载 4 · 预热 1" }).click();
  const dialog = page.getByRole("dialog", { name: "并发" });
  const fields = ["同时采集达人", "同时下载", "同时预热账号"];
  for (const field of fields) await dialog.getByLabel(field).fill("0");
  await dialog.getByRole("button", { name: "保存" }).click();
  await expect(page.getByText("crawler-1 下一次心跳（15 秒内）按新的并发运行")).toBeVisible();
  await expect(dialog).toBeHidden();

  await page.getByRole("button", { name: "采集 0 · 下载 0 · 预热 0" }).click();
  for (const field of fields) await expect(dialog.getByLabel(field)).toHaveValue("0");
});

test("爬取程序显示资料、列表和下载三个阶段", async ({ page }) => {
  const profile = {
    task_id: "00000000-0000-4000-8000-000000000001",
    handle: "alice",
    stage: "profile",
    pages_read: 0,
    works_found: 0,
    max_works: 5,
  };
  const listing = {
    ...profile,
    task_id: "00000000-0000-4000-8000-000000000002",
    handle: "bob",
    stage: "listing",
    pages_read: 1,
    works_found: 2,
  };
  await mockApi(page, {
    "GET /api/executors": [],
    "GET /api/executors/crawler/crawl-progress": [
      { instance_id: "crawler-1", crawls: [profile] },
      { instance_id: "crawler-2", crawls: [listing] },
      { instance_id: "crawler-old", crawls: null },
    ],
    "GET /api/executors/crawler/download-progress": [],
    "GET /api/identities": { target: 8, devices: [], alerts: [] },
    "GET /api/proxy-alerts": [],
  });
  await page.goto("/executors");

  const info = page.getByRole("region", { name: "达人信息获取" });
  const works = page.getByRole("region", { name: "达人视频列表获取" });
  await expect(info).toContainText("@alice");
  await expect(info).toContainText("读取达人主页");
  await expect(works).toContainText("crawler-2");
  await expect(works).toContainText("已读 1 页");
  const bar = works.getByRole("progressbar", { name: "@bob 作品列表获取进度" });
  await expect(bar).toHaveAttribute("aria-valuetext", "候选 2/5 条");
  await expect(page.getByRole("heading", { name: "达人视频下载" })).toBeVisible();
  await expect(page.getByText("1 台设备暂未上报采集进度")).toBeVisible();

  profile.stage = "listing";
  listing.pages_read = 2;
  listing.works_found = 5;
  await expect(info).not.toContainText("@alice");
  await expect(works).toContainText("@alice");
  await expect(works).toContainText("已读 2 页 · 确认最新作品");
  await expect(bar).toHaveAttribute("aria-valuenow", "5");
  await expect(bar).toHaveAttribute("aria-valuemax", "5");
});

test("从执行程序直接修改模型请求并发", async ({ page }) => {
  const now = new Date().toISOString();
  const executor = {
    kind: "analysis",
    instance_id: "analysis-1",
    url: "http://analysis.test",
    version: "1.0",
    online: true,
    registered_at: now,
    last_seen_at: now,
    withdrawn_at: null,
  };
  const provider = { id: "provider-1", name: "测试供应商", provider: "openai" };
  const model = {
    id: "test-model",
    provider_id: provider.id,
    name: "vision-model",
    max_concurrency: 4,
    requests_per_minute: 60,
    timeout_seconds: 240,
    params: { temperature: 0 },
  };
  await mockApi(page, {
    "GET /api/executors": [executor],
    "GET /api/executors/analysis/status": {
      jobs: {},
      running: {},
      ready: {},
      models: [],
      free_disk_bytes: 0,
    },
    "GET /api/executors/crawler/status": {
      crawl: { running: 0, queued: 0 },
      download: { running: 0, queued: 0 },
      identities: null,
      leases: 0,
      paused_batches: [],
    },
    "GET /api/ai/models": [model],
    "GET /api/ai/providers": [provider],
    "PUT /api/ai/models/test-model": { ...model, max_concurrency: 8 },
    "GET /api/identities": { target: 8, devices: [], alerts: [] },
    "GET /api/proxy-alerts": [],
  });

  await page.goto("/executors");
  await page.getByText("分析 Worker 的部署并发设置", { exact: true }).click();
  await expect(page.getByText("VA_THREAD_POOL_SIZE", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "设置模型并发" }).click();
  await expect(page.getByRole("tab", { name: "模型", exact: true })).toHaveAttribute(
    "data-state",
    "active",
  );
  const row = page.getByRole("row", { name: /vision-model/ });
  await row.getByRole("button", { name: "编辑" }).click();
  const dialog = page.getByRole("dialog", { name: "编辑模型" });
  await dialog.getByRole("spinbutton", { name: "请求并发上限" }).fill("8");
  const sent = page.waitForRequest(
    (request) => request.method() === "PUT" && request.url().endsWith("/api/ai/models/test-model"),
  );
  await dialog.getByRole("button", { name: "保存" }).click();
  expect((await sent).postDataJSON()).toEqual({
    provider_id: provider.id,
    name: model.name,
    max_concurrency: 8,
    requests_per_minute: 60,
    timeout_seconds: 240,
    params: { temperature: 0 },
  });
  await expect(dialog).toBeHidden();
});
