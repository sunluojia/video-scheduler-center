import { expect, type Page, test } from "@playwright/test";
import { mockApi } from "./mock";

const now = new Date().toISOString();
const us = { id: "3f0c9a2e-6b1d-4e8a-9c55-2d7e4b1a0f11", name: "美国", parent_id: null, count: 0 };
const jp = { id: "8d2b7c41-0e5f-4a93-b6d8-1c4f2e9a7b30", name: "日本", parent_id: null, count: 0 };
const rule = {
  source: "direct",
  folder_id: null,
  local_port: null,
  mode: "priority",
  line_limit: 2,
  updated_at: now,
};
const card = (key: string, name: string) => ({
  key,
  service: "crawler",
  service_name: "爬取程序",
  name,
  rule,
  proxies: 1,
  lines: [],
  devices: [],
});
const proxy = {
  id: "5a1e3c7d-2b4f-4d6a-8e9c-0f1a2b3c4d5e",
  name: "",
  protocol: "http",
  host: "1.2.3.4",
  port: 8080,
  username: "",
  has_password: false,
  has_private_key: false,
  dynamic: false,
  folder_id: null,
  uses: ["crawler.download_video"],
  states: [{ use: "crawler.download_video", state: "open", cooldown_until: null, reason: null }],
  enabled: true,
  status: "available",
  testing: false,
  error: null,
  exit_ip: "1.2.3.4",
  ip_version: 4,
  line: "1.2.3.4",
  latency_ms: 120,
  tested_at: now,
  tested_by: "crawler-1",
  speed_testing: false,
  speed_mbps: 32.3,
  parallel_mbps: 101.3,
  speed_tested_at: now,
  speed_error: null,
  in_use: 0,
  recent_ok: 0,
  recent_failed: 0,
  created_at: now,
};
const common = {
  "GET /api/proxies": { items: [proxy], total: 1 },
  "GET /api/proxy-folders": { folders: [us, jp], unfiled: 1 },
  "GET /api/proxy-uses": [
    card("crawler.crawl_creator", "获取信息"),
    card("crawler.download_video", "下载视频"),
  ],
  "GET /api/proxy-alerts": [],
  "GET /api/executors": [],
};

/** 等下一个这种请求发出，返回它的请求体。 */
const body = (page: Page, method: string, path: string) =>
  page
    .waitForRequest((request) => request.method() === method && request.url().endsWith(path))
    .then((request) => request.postDataJSON());

test("导入代理：选文件夹和用途，预览后导入", async ({ page }) => {
  await mockApi(page, {
    ...common,
    "POST /api/proxies/parse": [
      {
        number: 1,
        host: "5.6.7.8",
        port: 8080,
        protocol: "auto",
        username: "",
        has_password: false,
        error: null,
        duplicate: false,
      },
      { number: 2, has_password: false, error: "无法识别这一行", duplicate: false },
    ],
    "POST /api/proxies/import": { created: 1, skipped: 1 },
  });

  await page.goto(`/proxies?folder=${jp.id}`);
  await page.getByRole("button", { name: "导入" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("代理列表").fill("5.6.7.8:8080\n不是代理");
  await expect(dialog.getByLabel("放进文件夹")).toHaveText("日本");
  await dialog.getByRole("button", { name: "下载视频" }).click();
  await dialog.getByRole("button", { name: "预览" }).click();
  await expect(dialog.getByText("无法识别这一行")).toBeVisible();

  const sent = body(page, "POST", "/api/proxies/import");
  await dialog.getByRole("button", { name: "导入 1 个代理" }).click();
  expect(await sent).toMatchObject({ folder_id: jp.id, uses: ["crawler.download_video"] });
  await expect(page.getByText("已导入 1 个代理，跳过 1 行，稍后自动测试")).toBeVisible();
  await expect(dialog).toBeHidden();
});

test("下载视频改成走代理池轮询", async ({ page }) => {
  await mockApi(page, {
    ...common,
    "PUT /api/proxy-uses/crawler.download_video/rule": { ...rule, source: "pool" },
  });

  await page.goto("/proxies");
  // 同时含标题和规则说明的最内层 div 就是这张卡片
  const card = page
    .locator("div", { has: page.getByRole("heading", { name: "下载视频" }) })
    .filter({ hasText: "直连：用各设备自己的网络" })
    .last();
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "设置" }).click();
  const sheet = page.getByRole("dialog");
  await sheet.getByRole("button", { name: "代理池" }).click();
  await sheet.getByRole("button", { name: "轮询" }).click();

  const sent = body(page, "PUT", "/rule");
  await sheet.getByRole("button", { name: "保存" }).click();
  expect(await sent).toEqual({ source: "pool", folder_id: null, mode: "rotate", line_limit: 2 });
});

test("拖动换文件夹：代理拖进文件夹，文件夹拖到另一个前面", async ({ page }) => {
  await mockApi(page, {
    ...common,
    "POST /api/proxies/move": { count: 1 },
    [`POST /api/proxy-folders/${jp.id}/move`]: null,
  });

  await page.goto("/proxies");
  const folder = (name: string) => page.getByRole("treeitem", { name: new RegExp(name) });

  const moved = body(page, "POST", "/api/proxies/move");
  await page.getByTitle(/拖到左侧文件夹/).dragTo(folder("美国"));
  expect(await moved).toEqual({ ids: [proxy.id], folder_id: us.id });
  await expect(page.getByText("已把 1 个代理移到“美国”")).toBeVisible();

  const placed = body(page, "POST", `/api/proxy-folders/${jp.id}/move`);
  await folder("日本").dragTo(folder("美国"), { targetPosition: { x: 40, y: 2 } });
  expect(await placed).toEqual({ parent_id: null, position: 0 });
});

test("勾选代理测速，表里显示单路和并发速度", async ({ page }) => {
  await mockApi(page, { ...common, "POST /api/proxies/speed": { count: 1 } });

  await page.goto("/proxies");
  await expect(page.getByText("32 / 101 Mbps")).toBeVisible();

  await page.getByLabel("选择 1.2.3.4:8080").check();
  const sent = body(page, "POST", "/api/proxies/speed");
  await page.getByRole("button", { name: "测速" }).click();
  expect(await sent).toEqual({ ids: [proxy.id] });
  await expect(page.getByText(/已安排测速 1 个代理/)).toBeVisible();
});
