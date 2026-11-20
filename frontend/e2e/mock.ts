import type { Page } from "@playwright/test";

/** 拦截后端接口（只匹配 /api/ 开头的路径，避开开发服务器的 /src/api/ 模块）；实时推送返回空流。 */
export async function mockApi(page: Page, routes: Record<string, unknown>) {
  await page.route(
    (url) => url.pathname.startsWith("/api/"),
    async (route) => {
      const url = new URL(route.request().url());
      const key = `${route.request().method()} ${url.pathname}`;
      if (url.pathname === "/api/live") {
        return route.fulfill({ contentType: "text/event-stream", body: "" });
      }
      if (key in routes) return route.fulfill({ json: routes[key] });
      return route.fulfill({ status: 404, json: { detail: `未模拟 ${key}` } });
    },
  );
}
