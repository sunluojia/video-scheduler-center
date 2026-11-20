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
  targets: null,
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
  "GET /api/executors/crawler/download-progress": [],
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

test("下载视频从多个分组和单个代理按权重调度", async ({ page }) => {
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
  await sheet.getByRole("button", { name: "权重", exact: true }).click();
  await sheet.getByRole("button", { name: "指定分组和代理" }).click();
  await sheet.getByRole("checkbox", { name: "美国" }).check();
  await sheet.getByRole("checkbox", { name: "日本" }).check();
  await sheet.getByRole("spinbutton", { name: "美国的权重" }).fill("3");
  await sheet.getByRole("button", { name: "单个代理" }).click();
  await sheet.getByRole("textbox", { name: "搜索代理" }).fill("1.2.3.4");
  await sheet.getByRole("checkbox", { name: "1.2.3.4:8080" }).check();
  await sheet.getByRole("spinbutton", { name: "每条线路的并发上限" }).fill("4");

  const sent = body(page, "PUT", "/rule");
  await sheet.getByRole("button", { name: "保存" }).click();
  expect(await sent).toEqual({
    source: "pool",
    mode: "weighted",
    line_limit: 4,
    targets: [
      { kind: "folder", id: us.id, priority: 1, weight: 3 },
      { kind: "folder", id: jp.id, priority: 1, weight: 1 },
      { kind: "proxy", id: proxy.id, priority: 1, weight: 1 },
    ],
  });
});

test("下载进度和各设备的合计下载速度自动刷新", async ({ page }) => {
  const video = {
    task_id: "00000000-0000-4000-8000-000000000001",
    work_id: "7001",
    stage: "downloading",
    received_bytes: 1024,
    total_bytes: 4096,
  };
  const unknown = {
    ...video,
    task_id: "00000000-0000-4000-8000-000000000002",
    work_id: "7002",
    total_bytes: null,
  };
  const firstDevice = {
    instance_id: "crawler-1",
    downloads: [video],
    download_bytes_per_second: 1024 ** 2,
  };
  await mockApi(page, {
    ...common,
    "GET /api/executors/crawler/download-progress": [
      firstDevice,
      { instance_id: "crawler-2", downloads: [unknown], download_bytes_per_second: 512 * 1024 },
      { instance_id: "crawler-legacy", downloads: null },
    ],
  });

  await page.goto("/proxies");
  const known = page.getByRole("progressbar", { name: "视频 7001 下载进度" });
  const undetermined = page.getByRole("progressbar", { name: "视频 7002 下载进度" });
  await expect(known).toHaveAttribute("aria-valuenow", "25");
  await expect(undetermined).toBeVisible();
  await expect(undetermined).not.toHaveAttribute("aria-valuenow");
  await expect(page.getByText("1.0 KB · 总大小未知")).toBeVisible();
  await expect(page.getByText("1 台爬取设备暂时未提供下载进度")).toBeVisible();
  const speed = page.getByTitle("在线爬取设备最近 5 秒的合计视频下载速度");
  await expect(speed).toContainText("总下载速度 1.5 MB/s");
  await expect(speed).toContainText("2/3 台设备已上报");

  video.received_bytes = 3072;
  firstDevice.download_bytes_per_second = 2 * 1024 ** 2;
  await expect(known).toHaveAttribute("aria-valuenow", "75");
  await expect(speed).toContainText("2.5 MB/s");
  await page.getByRole("navigation").getByRole("link", { name: "执行程序" }).click();
  await expect(known).toHaveAttribute("aria-valuenow", "75");
  await expect(speed).toContainText("2.5 MB/s");
});

test("动态代理刷新出口时显示正在等待的任务数", async ({ page }) => {
  await mockApi(page, {
    ...common,
    "GET /api/proxies": {
      items: [{ ...proxy, status: "refreshing", dynamic: true, testing: true, in_use: 2 }],
      total: 1,
    },
  });
  await page.goto("/proxies");
  await expect(page.getByText("等待 2 个任务归还线路")).toBeVisible();
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
