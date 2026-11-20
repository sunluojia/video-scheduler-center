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

test("在页面上改爬取程序的并发", async ({ page }) => {
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
  };
  await mockApi(page, {
    "GET /api/executors": [crawler],
    "GET /api/identities": { target: 8, devices: [], alerts: [] },
    "GET /api/proxy-alerts": [],
    "PUT /api/executors/crawler/crawler-1/slots": { ...crawler, crawl_slots: 4 },
  });
  await page.goto("/executors");

  await page.getByRole("button", { name: "采集 2 · 下载 4" }).click();
  const dialog = page.getByRole("dialog", { name: "并发" });
  await dialog.getByLabel("同时采集达人").fill("4");
  const sent = page.waitForRequest((request) => request.url().endsWith("/slots"));
  await dialog.getByRole("button", { name: "保存" }).click();
  expect((await sent).postDataJSON()).toEqual({ crawl_slots: 4, download_slots: 4 });
  await expect(page.getByText("crawler-1 下一次心跳（15 秒内）按新的并发运行")).toBeVisible();
});
