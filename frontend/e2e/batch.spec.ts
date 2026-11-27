import { expect, test } from "@playwright/test";
import { mockApi } from "./mock";

const BATCH_ID = "0a7f6a59-4b5c-4c4e-9a52-3e0d1f0c2a10";
const now = new Date().toISOString();
const batch = {
  id: BATCH_ID,
  name: "女装达人",
  source: "links",
  status: "running",
  ai_batch_mode: false,
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
  const planned = { ...batch, ai_batch_mode: true };
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
    "POST /api/batches": planned,
    "GET /api/batches": { items: [planned], total: 1 },
    "GET /api/executors": [],
    [`GET /api/batches/${BATCH_ID}`]: planned,
    [`GET /api/batches/${BATCH_ID}/creators`]: { items: [creator], total: 1 },
  });

  await page.goto("/batches/new");
  await page.getByLabel("批次名称").fill("女装达人");
  await page.getByRole("tab", { name: "粘贴链接" }).click();
  await page.getByRole("textbox").last().fill("https://www.tiktok.com/@alice\n不是链接");
  await page.getByRole("button", { name: "解析预览" }).click();

  await expect(page.getByText("无法识别").first()).toBeVisible();
  await expect(page.getByRole("cell", { name: "alice", exact: true })).toBeVisible();

  const mode = page.getByRole("switch", { name: /Batch API/ });
  await expect(mode).not.toBeChecked();
  await mode.click();
  await expect(mode).toBeChecked();
  const request = page.waitForRequest(
    (item) => item.method() === "POST" && new URL(item.url()).pathname === "/api/batches",
  );
  await page.getByRole("button", { name: "保存并开始" }).click();
  expect((await request).postData()).toMatch(/name="ai_batch_mode"\r?\n\r?\ntrue/);
  await expect(page).toHaveURL(`/batches/${BATCH_ID}`);
  await expect(page.getByRole("switch", { name: "AI 批量模式" })).toBeChecked();
  await expect(page.getByRole("heading", { name: "女装达人" })).toBeVisible();
  await expect(page.getByRole("cell", { name: /alice/ }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "暂停采集" })).toBeVisible();
  await expect(page.getByRole("link", { name: "数据看板" }).last()).toHaveAttribute(
    "href",
    `/stats?batch=${BATCH_ID}`,
  );

  // 展开达人，直接看到每条作品的标签
  await page.getByRole("button", { name: "展开 alice 的作品" }).click();
  await expect(page.getByText("碎花连衣裙")).toBeVisible();
});

test("批次排障明细独立下载为 CSV", async ({ page }) => {
  await mockApi(page, {
    "GET /api/batches": { items: [batch], total: 1 },
    [`GET /api/batches/${BATCH_ID}`]: batch,
    [`GET /api/batches/${BATCH_ID}/creators`]: { items: [creator], total: 1 },
  });
  await page.route(`**/api/batches/${BATCH_ID}/execution-attempts`, (route) =>
    route.fulfill({
      contentType: "text/csv; charset=utf-8",
      body: "执行器,状态\ncrawler-b,succeeded",
    }),
  );

  await page.goto(`/batches/${BATCH_ID}`);
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "执行排障明细" }).click();
  expect((await download).suggestedFilename()).toBe("女装达人-执行排障明细.csv");
  await expect(page.getByRole("button", { name: "导出 Excel" })).toBeEnabled();
});

test("达人与作品详情展示跨设备尝试及历史未记录", async ({ page }) => {
  const work = creator.works[0];
  const crawl = {
    id: "crawl-task",
    kind: "crawl",
    run: 1,
    status: "succeeded",
    conclusion: "ok",
    error: null,
    published_at: now,
    accepted_at: now,
    finished_at: now,
    last_started_executor_id: "crawler-a",
    result_executor_id: null,
  };
  const attempt = {
    attempt_id: "10a7bbf5-d76e-4946-8528-3e81855cea03",
    task_id: "download-task",
    run: 2,
    attempt_no: 1,
    executor_id: "crawler-a",
    status: "expired",
    claimed_at: now,
    ended_at: now,
    error: "任务租约到期",
  };
  const longId = `crawler-b-${"device".repeat(18)}`;
  await mockApi(page, {
    [`GET /api/creators/${creator.id}`]: {
      creator,
      batch,
      crawl,
      summary: null,
      works: [work],
      crawl_attempts: [],
      summary_attempts: [],
      history: [],
    },
    [`GET /api/works/${work.id}`]: {
      creator,
      work,
      download: { ...crawl, kind: "download", run: 2, result_executor_id: longId },
      analysis: null,
      download_attempts: [
        attempt,
        {
          ...attempt,
          attempt_no: 2,
          error: null,
          status: "succeeded",
          executor_id: longId,
          attempt_id: "5f121bcc-6934-4849-ac02-377aec9ba8b4",
        },
      ],
      analysis_attempts: [],
      stages: [],
      history: [],
    },
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/creators/${creator.id}`);
  const task = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "爬取任务" }) });
  await expect(task.getByText("crawler-a")).toBeVisible();
  await expect(task.getByText("历史未记录")).toBeVisible();
  await page.getByRole("link", { name: /夏天的通勤穿搭/ }).click();
  await expect(page.getByRole("heading", { name: "夏天的通勤穿搭" })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  const attempts = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "执行尝试" }) });
  await expect(attempts.getByText("租约过期")).toBeVisible();
  await expect(attempts.getByText("任务租约到期")).toBeVisible();
  await expect(attempts.getByText(longId)).toBeVisible();
  await expect(attempts.getByText("历史未记录")).toBeVisible();
  const name = await attempts.getByText(longId).boundingBox();
  expect(name!.x + name!.width).toBeLessThanOrEqual(390);
});

test("导出 Excel 等待期间禁用重复点击，失败后可以重试", async ({ page }) => {
  await mockApi(page, {
    "GET /api/batches": { items: [batch], total: 1 },
    "GET /api/executors": [],
    [`GET /api/batches/${BATCH_ID}`]: batch,
    [`GET /api/batches/${BATCH_ID}/creators`]: { items: [creator], total: 1 },
  });

  let requests = 0;
  let finishFirstRequest!: () => void;
  const firstRequest = new Promise<void>((resolve) => {
    finishFirstRequest = resolve;
  });
  await page.route(`**/api/batches/${BATCH_ID}/export`, async (route) => {
    requests += 1;
    if (requests === 1) {
      await firstRequest;
      await route.fulfill({ status: 503, json: { detail: "暂时无法生成 Excel" } });
      return;
    }
    await route.fulfill({
      contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      body: Buffer.from([0x50, 0x4b, 0x03, 0x04]),
    });
  });

  await page.goto(`/batches/${BATCH_ID}`);
  const button = page.getByRole("button", { name: "导出 Excel" });
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "正在生成 Excel…" })).toBeDisabled();
  await expect(page.getByRole("status", { name: "Excel 导出进度" })).toHaveText(
    "正在生成 Excel，大批次可能需要等待一段时间。",
  );
  await expect.poll(() => requests).toBe(1);
  finishFirstRequest();

  await expect(page.getByText("暂时无法生成 Excel")).toBeVisible();
  await expect(button).toBeEnabled();
  await page.setViewportSize({ width: 390, height: 844 });
  const downloadEvent = page.waitForEvent("download");
  await button.click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe("女装达人-达人标签结果.xlsx");
  expect(await download.failure()).toBeNull();
  await expect(page.getByText("Excel 已生成，已发起下载")).toBeVisible();
  await expect(page).toHaveURL(`/batches/${BATCH_ID}`);
  expect(requests).toBe(2);
});

test("Excel 生成超时后提示并恢复导出按钮", async ({ page }) => {
  await mockApi(page, {
    "GET /api/batches": { items: [batch], total: 1 },
    "GET /api/executors": [],
    [`GET /api/batches/${BATCH_ID}`]: batch,
    [`GET /api/batches/${BATCH_ID}/creators`]: { items: [creator], total: 1 },
  });
  await page.route(`**/api/batches/${BATCH_ID}/export`, () => {
    // 保持请求挂起，验证界面能主动结束等待。
  });
  await page.clock.install();
  await page.goto(`/batches/${BATCH_ID}`);
  const button = page.getByRole("button", { name: "导出 Excel" });
  await button.click();
  await expect(page.getByRole("button", { name: "正在生成 Excel…" })).toBeDisabled();
  await page.clock.fastForward(180_001);
  await expect(page.getByText("生成 Excel 超时，请重试")).toBeVisible();
  await expect(button).toBeEnabled();
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

test("运行中的批次可以切换 AI 批量模式并处理保存失败", async ({ page }) => {
  let enabled = false;
  let fail = false;
  await mockApi(page, {
    [`GET /api/batches/${BATCH_ID}/creators`]: { items: [creator], total: 1 },
  });
  await page.route(`**/api/batches?*`, (route) =>
    route.fulfill({ json: { items: [{ ...batch, ai_batch_mode: enabled }], total: 1 } }),
  );
  await page.route(`**/api/batches/${BATCH_ID}`, (route) =>
    route.fulfill({ json: { ...batch, ai_batch_mode: enabled } }),
  );
  await page.route(`**/api/batches/${BATCH_ID}/ai-batch-mode`, (route) => {
    expect(route.request().method()).toBe("PUT");
    if (fail) return route.fulfill({ status: 503, json: { detail: "分析服务暂不可用" } });
    enabled = route.request().postDataJSON().enabled;
    return route.fulfill({ json: { batch: { ...batch, ai_batch_mode: enabled }, warnings: [] } });
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/batches/${BATCH_ID}`);
  const mode = page.getByRole("switch", { name: "AI 批量模式" });
  await expect(mode).not.toBeChecked();
  await expect(mode).toHaveAccessibleDescription(/听音.*实时推理/);
  await mode.press("Space");
  await expect(mode).toBeChecked();
  await expect(page.getByText("AI 批量", { exact: true })).toHaveCount(2);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(mode).toBeVisible();
  fail = true;
  await mode.click();
  await expect(page.getByText("分析服务暂不可用")).toBeVisible();
  await expect(mode).toBeChecked();
  fail = false;
  await mode.click();
  await expect(mode).not.toBeChecked();
});
