import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import type { Schemas } from "@/api/client";
import { Layout } from "@/app/layout";
import { dateTime } from "@/lib/format";
import { ExecutorsPage } from "./executors";

vi.mock("@/api/live", () => ({ useLiveUpdates: vi.fn() }));
afterEach(cleanup);

type Executor = Schemas["ExecutorView"];

const online: Executor = {
  kind: "crawler",
  instance_id: "crawler-online",
  url: "http://crawler.test:8090",
  version: "2.0",
  registered_at: "2026-11-23T06:00:00Z",
  last_seen_at: "2026-11-25T06:00:00Z",
  withdrawn_at: null,
  online: true,
  crawl_slots: 2,
  download_slots: 4,
  warm_slots: 1,
};
const withdrawnAt = "2026-11-25T05:59:00Z";
const retired: Executor = {
  ...online,
  instance_id: "crawler-retired",
  online: false,
  last_seen_at: "2026-11-25T05:58:00Z",
  withdrawn_at: withdrawnAt,
  activity: { active: 0, completed_24h: 12, expired_24h: 1, takeovers_24h: 2 },
};

function renderExecutors(executors: Executor[]) {
  const client = new QueryClient({ defaultOptions: { queries: { enabled: false } } });
  client.setQueryData(["get", "/api/executors", {}], executors);
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/executors"]}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/executors" element={<ExecutorsPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("注销设备移出在线分母，展开历史后仍可查看登记、注销时间和 24 小时统计", async () => {
  const user = userEvent.setup();
  renderExecutors([online, { ...online, instance_id: "crawler-online-2" }, retired]);

  const count = screen.getByText("2/2 在线");
  expect(count.parentElement?.firstElementChild).toHaveClass("bg-status-done");
  expect(screen.getByText(retired.instance_id)).not.toBeVisible();

  await user.click(screen.getByText("历史设备（1）"));
  const row = within(screen.getByRole("row", { name: /crawler-retired/ }));
  expect(row.getByText("已注销")).toBeVisible();
  expect(row.getByText(dateTime(retired.registered_at))).toBeVisible();
  expect(row.getByText(dateTime(withdrawnAt))).toHaveAttribute("datetime", withdrawnAt);
  expect(row.getByText("完成 12 · 过期 1 · 接手 2")).toBeVisible();
  expect(row.queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
});

it("心跳超时但未注销的爬取设备仍计入总数并显示告警", () => {
  renderExecutors([
    online,
    {
      ...online,
      instance_id: "crawler-timed-out",
      last_seen_at: "2026-11-25T05:55:00Z",
      online: false,
    },
    retired,
  ]);

  const count = screen.getByText("1/2 在线");
  expect(count.parentElement?.firstElementChild).toHaveClass("bg-status-attention");
  const row = within(screen.getByRole("row", { name: /crawler-timed-out/ }));
  expect(row.getByText("离线")).toHaveClass("text-status-failed");
});

it("只有注销设备时使用中性状态，并保留历史入口", () => {
  renderExecutors([retired]);

  for (const count of screen.getAllByText("0/0 在线")) {
    expect(count.parentElement?.firstElementChild).toHaveClass("bg-sidebar-muted");
  }
  expect(screen.getByText("暂无在册执行程序")).toBeVisible();
  expect(screen.getByText("历史设备（1）")).toBeVisible();
});
