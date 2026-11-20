import { defineConfig } from "@playwright/test";

// 端到端测试拦截 /api 请求返回模拟数据，只需要启动前端开发服务器
export default defineConfig({
  testDir: "e2e",
  use: { baseURL: "http://127.0.0.1:5173", locale: "zh-CN", timezoneId: "Asia/Shanghai" },
  webServer: {
    command: "pnpm dev --host 127.0.0.1 --port 5173 --strictPort",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
  },
});
