import { expect, test } from "@playwright/test";
import { mockApi } from "./mock";

const bailian = {
  id: "bailian",
  name: "阿里云百炼",
  provider: "openai",
  base_url: "https://ws-example.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
};

test("显式缓存开关保存并恢复模型设置", async ({ page }) => {
  const generationParams = { temperature: 0, extra_body: { enable_thinking: false } };
  let model = {
    id: "flash",
    provider_id: bailian.id,
    name: "qwen3.8-flash",
    max_concurrency: 4,
    requests_per_minute: 60,
    timeout_seconds: 240,
    params: generationParams,
  };
  await mockApi(page, {
    "GET /api/ai/providers": [bailian],
    "GET /api/ai/routes": [],
    "GET /api/identities": { target: 8, devices: [], alerts: [] },
    "GET /api/proxy-alerts": [],
  });
  await page.route("**/api/ai/models", (route) => route.fulfill({ json: [model] }));
  await page.route("**/api/ai/models/flash", (route) => {
    model = { ...route.request().postDataJSON(), id: "flash" };
    return route.fulfill({ json: model });
  });

  await page.goto("/ai");
  await page.getByRole("tab", { name: "模型", exact: true }).click();
  const row = page.getByRole("row", { name: /qwen3.8-flash/ });
  const edit = row.getByRole("button", { name: "编辑" });
  await edit.click();
  const dialog = page.getByRole("dialog", { name: "编辑模型" });
  const cacheSwitch = dialog.getByRole("switch", { name: "显式 Prompt Cache" });
  await expect(cacheSwitch).not.toBeChecked();
  await cacheSwitch.press("Space");
  await expect(cacheSwitch).toBeChecked();
  await dialog.getByRole("button", { name: "保存" }).click();
  await expect(row).toContainText("bailian_explicit_cache");
  expect(model.params).toEqual({ ...generationParams, bailian_explicit_cache: true });

  await edit.click();
  await expect(cacheSwitch).toBeChecked();
  await expect(dialog.getByRole("textbox", { name: "调用参数" })).not.toHaveValue(
    /bailian_explicit_cache/,
  );
  await cacheSwitch.click();
  await dialog.getByRole("button", { name: "保存" }).click();
  await expect(row).not.toContainText("bailian_explicit_cache");
  expect(model.params).toEqual(generationParams);
  await edit.click();
  await expect(cacheSwitch).not.toBeChecked();
});

test("只有兼容的模型配置显示显式缓存开关", async ({ page }) => {
  await mockApi(page, {
    "GET /api/ai/providers": [bailian, { id: "openai", name: "OpenAI", provider: "openai" }],
    "GET /api/ai/routes": [],
    "GET /api/ai/models": [],
    "GET /api/identities": { target: 8, devices: [], alerts: [] },
    "GET /api/proxy-alerts": [],
  });

  await page.goto("/ai");
  await page.getByRole("tab", { name: "模型", exact: true }).click();
  await page.getByRole("button", { name: "新增" }).click();
  const dialog = page.getByRole("dialog", { name: "新增模型" });
  const cacheSwitch = dialog.getByRole("switch", { name: "显式 Prompt Cache" });
  const name = dialog.getByRole("textbox", { name: "模型名" });
  await name.fill("qwen3.8-omni-flash");
  await expect(cacheSwitch).toHaveCount(0);
  await name.fill("qwen3.8-flash");
  await expect(cacheSwitch).toBeVisible();

  const params = dialog.getByRole("textbox", { name: "调用参数" });
  await params.fill('{"use_responses_api": true}');
  await expect(cacheSwitch).toHaveCount(0);
  await params.fill("{}");
  await expect(cacheSwitch).toBeVisible();
  await dialog.getByRole("combobox", { name: "供应商" }).click();
  await page.getByRole("option", { name: "OpenAI", exact: true }).click();
  await expect(cacheSwitch).toHaveCount(0);
});
