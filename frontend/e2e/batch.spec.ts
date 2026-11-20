import { expect, test } from "@playwright/test";
import { mockApi } from "./mock";

const BATCH_ID = "0a7f6a59-4b5c-4c4e-9a52-3e0d1f0c2a10";
const now = new Date().toISOString();
const batch = {
  id: BATCH_ID,
  name: "女装达人",
  source: "links",
  status: "running",
  created_at: now,
  started_at: now,
  finished_at: null,
  creators: { completed: 1, crawling: 1 },
  creator_progress: { completed: 1, crawling: 1 },
  videos: { completed: 4, analyzing: 1 },
};
const creator = {
  id: "5b1d2f0e-8c1a-4c55-b7a0-6f3c9d2e4a11",
  batch_id: BATCH_ID,
  position: 1,
  row: null,
  name: "alice",
  handle: "alice",
  oecuid: null,
  status: "completed",
  progress: "completed",
  note: null,
  labels: [],
  tags: ["连衣裙", "通勤"],
  profile: null,
  updated_at: now,
  works: [
    {
      id: "7c2e4b1a-3d5f-4a6b-9c8d-0e1f2a3b4c5d",
      batch_id: BATCH_ID,
      creator_id: "5b1d2f0e-8c1a-4c55-b7a0-6f3c9d2e4a11",
      position: 1,
      platform_id: "7300000000000000001",
      url: "https://www.tiktok.com/@alice/video/7300000000000000001",
      kind: "video",
      published_at: now,
      description: "夏天的通勤穿搭",
      topics: [],
      status: "completed",
      error: null,
      tags: ["碎花连衣裙"],
      updated_at: now,
    },
  ],
};

test("粘贴链接新建批次并进入详情", async ({ page }) => {
  await mockApi(page, {
    "POST /api/imports/preview": {
      source: "links",
      rows: [
        {
          row: null,
          name: "https://www.tiktok.com/@alice",
          handle: "alice",
          oecuid: null,
          has_qr: false,
        },
        { row: null, name: "不是链接", handle: null, oecuid: null, has_qr: false },
      ],
      invalid: 1,
    },
    "POST /api/batches": batch,
    "GET /api/batches": { items: [batch], total: 1 },
    "GET /api/executors": [],
    [`GET /api/batches/${BATCH_ID}`]: batch,
    [`GET /api/batches/${BATCH_ID}/creators`]: { items: [creator], total: 1 },
  });

  await page.goto("/batches/new");
  await page.getByLabel("批次名称").fill("女装达人");
  await page.getByRole("tab", { name: "粘贴链接" }).click();
  await page.getByRole("textbox").last().fill("https://www.tiktok.com/@alice\n不是链接");
  await page.getByRole("button", { name: "解析预览" }).click();

  await expect(page.getByText("无法识别").first()).toBeVisible();
  await expect(page.getByRole("cell", { name: "alice", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "保存并开始" }).click();
  await expect(page).toHaveURL(`/batches/${BATCH_ID}`);
  await expect(page.getByRole("heading", { name: "女装达人" })).toBeVisible();
  await expect(page.getByRole("cell", { name: /alice/ }).first()).toBeVisible();

  // 展开达人，直接看到每条作品的标签
  await page.getByRole("button", { name: "展开 alice 的作品" }).click();
  await expect(page.getByText("碎花连衣裙")).toBeVisible();
});

test("达人详情显示爬取程序回传的平台资料", async ({ page }) => {
  const { works, ...view } = creator;
  const profile = {
    handle: "alice",
    url: "https://www.tiktok.com/@alice",
    nickname: "Alice 的衣橱",
    platform_user_id: "6800000000000000001",
  };
  await mockApi(page, {
    [`GET /api/creators/${view.id}`]: {
      creator: { ...view, profile },
      batch,
      crawl: null,
      works,
      summary: null,
      history: [],
    },
  });

  await page.goto(`/creators/${view.id}`);
  await expect(page.getByText("平台资料")).toBeVisible();
  await expect(page.getByText(profile.nickname)).toBeVisible();
  await expect(page.getByText(profile.platform_user_id)).toBeVisible();
  await expect(page.getByRole("link", { name: "@alice" }).last()).toHaveAttribute(
    "href",
    profile.url,
  );
});

test("工作台显示十种达人进度并按所选阶段筛选", async ({ page }) => {
  const workbench = {
    ...batch,
    creators: { analyzing: 3 },
    creator_progress: { downloading: 1, audio_analysis: 1, video_analysis: 1 },
  };
  const audio = { ...creator, name: "音频达人", status: "analyzing", progress: "audio_analysis" };
  const visual = { ...audio, id: "visual-creator", name: "视频达人", progress: "video_analysis" };
  await mockApi(page, {
    "GET /api/batches": { items: [workbench], total: 1 },
    [`GET /api/batches/${BATCH_ID}`]: workbench,
  });
  await page.route(
    (url) => url.pathname === `/api/batches/${BATCH_ID}/creators`,
    (route) => {
      const query = new URL(route.request().url()).searchParams.get("progress");
      const items = [audio, visual].filter((item) => !query || item.progress === query);
      return route.fulfill({ json: { items, total: items.length } });
    },
  );

  await page.goto(`/batches/${BATCH_ID}`);
  const stages = page.getByRole("group", { name: "处理阶段" });
  await expect(stages.getByRole("button")).toHaveText([
    "待爬取0",
    "爬取中0",
    "下载中1",
    "音频分析中1",
    "视频分析中1",
    "汇总中0",
  ]);
  const results = page.getByRole("group", { name: "处理结果" });
  await expect(results.getByRole("button")).toHaveText([
    "已完成0",
    "失败账号0",
    "需要重新抓取0",
    "无法识别0",
  ]);
  await expect(page.getByRole("link", { name: "视频达人" })).toBeVisible();

  await stages.getByRole("button", { name: "音频分析中 1" }).click();
  await expect(page).toHaveURL(/status=audio_analysis/);
  await expect(page.getByRole("row", { name: /音频达人/ })).toContainText("音频分析中");
  await expect(page.getByRole("link", { name: "视频达人" })).toHaveCount(0);
  await page.getByRole("button", { name: "全部达人 3" }).click();
  await expect(page.getByRole("link", { name: "视频达人" })).toBeVisible();
});

test("已删除批次的旧链接会回到空工作台", async ({ page }) => {
  await mockApi(page, { "GET /api/batches": { items: [], total: 0 } });

  await page.goto(`/batches/${BATCH_ID}?tab=problems`);
  await expect(page).toHaveURL(/\/batches$/);
  await expect(page.getByText("还没有批次，点“新建批次”上传名单或粘贴链接。")).toBeVisible();
});

test("在工作台删除批次后会打开仍存在的批次", async ({ page }) => {
  const validBatch = { ...batch, id: "728d69e6-76dc-4c9d-8757-327af7cfaa53", name: "另一个批次" };
  const deletedPath = `/api/batches/${BATCH_ID}`;
  let deleted = false;
  await mockApi(page, {
    [`GET /api/batches/${validBatch.id}`]: validBatch,
    [`GET /api/batches/${validBatch.id}/creators`]: { items: [], total: 0 },
    [`GET ${deletedPath}/creators`]: { items: [], total: 0 },
  });
  await page.route(
    (url) => url.pathname === "/api/batches",
    (route) => {
      const items = deleted ? [validBatch] : [batch, validBatch];
      return route.fulfill({ json: { items, total: items.length } });
    },
  );
  await page.route(
    (url) => url.pathname === deletedPath,
    (route) => {
      if (route.request().method() === "DELETE") {
        deleted = true;
        return route.fulfill({ status: 204 });
      }
      return deleted
        ? route.fulfill({ status: 404, json: { detail: "批次不存在" } })
        : route.fulfill({ json: { ...batch, status: "completed" } });
    },
  );

  await page.goto("/batches");
  await expect(page).toHaveURL(`/batches/${BATCH_ID}`);
  await page.getByRole("button", { name: "删除", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "删除", exact: true }).click();

  await expect(page).toHaveURL(`/batches/${validBatch.id}`);
  await expect(page.getByRole("heading", { name: validBatch.name })).toBeVisible();
  await expect(page.getByRole("link", { name: /女装达人/ })).toHaveCount(0);
});

test("后端暂时无法读取批次时显示错误信息", async ({ page }) => {
  await mockApi(page, { "GET /api/batches": { items: [], total: 0 } });
  await page.route(
    (url) => url.pathname === `/api/batches/${BATCH_ID}`,
    (route) => route.fulfill({ status: 503, json: { detail: "暂时无法读取批次" } }),
  );
  await page.goto(`/batches/${BATCH_ID}`);
  await expect(page.getByText("暂时无法读取批次")).toBeVisible();
  await expect(page).toHaveURL(`/batches/${BATCH_ID}`);
});
