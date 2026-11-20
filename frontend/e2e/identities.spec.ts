import { expect, test } from "@playwright/test";
import { mockApi } from "./mock";

const now = Date.now();
const at = (minutes: number) => new Date(now + minutes * 60_000).toISOString();
const identity = (id: string, state: string, extra: Record<string, unknown> = {}) => ({
  id,
  state,
  fails: 0,
  cooldown_until: null,
  token_expires_at: at(20),
  last_used_at: at(-2),
  warmed_at: at(-10),
  browser: "146",
  retired_at: null,
  retire_reason: null,
  ...extra,
});
const board = {
  target: 3,
  devices: [
    {
      instance_id: "crawler-1",
      online: true,
      last_seen_at: at(0),
      snapshot: {
        target: 3,
        ready: 2,
        in_use: 1,
        warming: 0,
        cooling: 1,
        total: 3,
        short_since: at(-12),
        warm_total: 4,
        warm_failed: 3,
        list_total: 10,
        list_empty: 1,
        download_total: 8,
        download_blocked: 1,
        items: [
          identity("a1", "ready"),
          identity("b2", "in_use"),
          identity("c3", "cooling", { fails: 1, cooldown_until: at(5), token_expires_at: null }),
          identity("d4", "retired", { retired_at: at(-60), retire_reason: "下载连续 6 次被拒" }),
        ],
      },
    },
  ],
  alerts: ["设备 crawler-1 可用身份 2/3，已经 12 分钟不够；最近一小时预热 4 次，成功 1 次"],
};
const common = {
  "GET /api/identities": board,
  "GET /api/proxy-alerts": [],
  "GET /api/executors": [],
};

test("账号池：设备卡片、问题提示，默认不显示已退休的", async ({ page }) => {
  await mockApi(page, common);
  await page.goto("/identities");

  await expect(page.getByText(board.alerts[0])).toBeVisible();
  await expect(page.getByRole("link", { name: /账号池\s*1/ })).toBeVisible();
  await expect(page.getByText("预热失败 75%")).toBeVisible();
  const rows = page.getByRole("row");
  await expect(rows.filter({ hasText: "a1" })).toContainText("可用");
  await expect(rows.filter({ hasText: "c3" })).toContainText("下载被拒，冷却到");
  await expect(rows.filter({ hasText: "d4" })).toHaveCount(0);

  await page.getByRole("button", { name: "已退休" }).click();
  await expect(rows.filter({ hasText: "d4" })).toContainText("下载连续 6 次被拒");
  await expect(rows.filter({ hasText: "a1" })).toHaveCount(0);
});

test("退休一个身份：确认后转给设备，正在用的不能操作", async ({ page }) => {
  await mockApi(page, { ...common, "POST /api/identities/crawler-1/a1/retire": null });
  await page.goto("/identities");

  const busy = page.getByRole("row").filter({ hasText: "b2" });
  await expect(busy.getByRole("button", { name: "退休" })).toBeDisabled();

  await page
    .getByRole("row")
    .filter({ hasText: "a1" })
    .getByRole("button", { name: "退休" })
    .click();
  const sent = page.waitForRequest((request) =>
    request.url().endsWith("/api/identities/crawler-1/a1/retire"),
  );
  await page.getByRole("alertdialog").getByRole("button", { name: "退休" }).click();
  expect((await sent).method()).toBe("POST");
  await expect(page.getByText("a1 已退休，后台会补一个新的")).toBeVisible();
});

test("改账号池要保持的数量", async ({ page }) => {
  await mockApi(page, { ...common, "PUT /api/identity-pool": { target: 12 } });
  await page.goto("/identities");

  await page.getByRole("button", { name: "至少保持 3 个可用" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("至少保持可用").fill("12");
  const sent = page.waitForRequest((request) => request.url().endsWith("/api/identity-pool"));
  await dialog.getByRole("button", { name: "保存" }).click();
  expect((await sent).postDataJSON()).toEqual({ target: 12 });
  await expect(page.getByText("已保存：至少保持 12 个可用")).toBeVisible();
  await expect(dialog).toBeHidden();
});

test("执行程序页的爬取程序卡片", async ({ page }) => {
  await mockApi(page, {
    ...common,
    "GET /api/executors/crawler/status": {
      crawl: { running: 1, queued: 4 },
      download: { running: 2, queued: 7 },
      identities: { target: 8, ready: 6 },
      leases: 3,
      paused_batches: [],
      download_paused_until: null,
    },
  });
  await page.goto("/executors");

  const card = page.locator("section", { has: page.getByRole("heading", { name: "爬取程序" }) });
  await expect(card.getByRole("link", { name: "6 / 8" })).toHaveAttribute("href", "/identities");
  await expect(card).toContainText("获取作品 4 · 下载 7");
  await expect(card).toContainText("暂停的批次无");
});
