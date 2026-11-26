import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { fetchClient, type Schemas } from "@/api/client";
import { chartBars, isHighRisk } from "./crawl-ip-view";
import {
  crawlIpHistory,
  crawlIpStats,
  historyBatch,
  noCrawlIpBatches,
  runningBatch,
} from "./crawl-ips.fixtures";
import { CrawlIpPanel } from "./crawl-ips";

const scrollIntoView = Element.prototype.scrollIntoView;
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  Element.prototype.scrollIntoView = scrollIntoView;
});

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
  render(
    <QueryClientProvider client={client}>
      <CrawlIpPanel />
    </QueryClientProvider>,
  );
}

function reply(data: Schemas["CrawlIpStats"] | Schemas["Page_CrawlIpBatch_"]) {
  return { data, response: new Response() };
}

it("图表按选中指标排序，用真实百分比作为比例，并跳过不可用时速", () => {
  const [first, second, third] = crawlIpStats.lines;
  const input = [third, second, first];
  expect(chartBars(input, "collected_creators").map(({ line }) => line.line)).toEqual([
    first.line,
    second.line,
    third.line,
  ]);
  expect(chartBars(input, "creator_share_pct").map(({ label, width }) => [label, width])).toEqual([
    ["60%", 60],
    ["30%", 30],
    ["10%", 10],
  ]);
  expect(chartBars([{ ...first, creators_per_hour: null }, second], "creators_per_hour")).toEqual([
    { line: second, label: "3 人/时", width: 100 },
  ]);
  expect(chartBars([third], "risk_count")[0].width).toBe(0);
  expect(input[0]).toBe(third);
  expect(isHighRisk(first)).toBe(true);
  expect(isHighRisk(second)).toBe(false);
  expect(isHighRisk({ ...first, crawl_leases: 4, risk_rate_pct: 100 })).toBe(false);
  expect(isHighRisk({ ...first, crawl_leases: 5, risk_rate_pct: 20 })).toBe(true);
});

it("显示默认批次的汇总、多 IP 图表、风控细分与退避状态", async () => {
  vi.spyOn(fetchClient, "GET").mockImplementation(async (path) =>
    path === "/api/stats/crawl-ip-batches"
      ? reply({ items: [runningBatch], total: 1 })
      : reply(crawlIpStats),
  );
  renderPanel();
  const detail = await screen.findByRole("table", { name: "采集 IP 明细" });
  expect(screen.getByRole("combobox", { name: "采集批次" })).toHaveTextContent(runningBatch.name);
  expect(screen.getByText("19.05%")).toBeVisible();
  expect(within(detail).getAllByRole("row")).toHaveLength(4);
  const high = within(detail).getByRole("row", { name: /直连美国家宽 01/ });
  expect(within(high).getByText("空正文")).toHaveTextContent("空正文 2");
  expect(within(high).getByText("退避中")).toBeVisible();
  expect(within(high).getByText("第 2 阶")).toBeVisible();
  expect(within(high).getByText("偏高")).toBeVisible();
  expect(within(high).getByText("私密 1")).toBeVisible();
  expect(within(detail).getAllByText("偏高")).toHaveLength(1);

  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "采集时速" }));
  expect(
    within(screen.getByRole("list", { name: "采集时速对比图" })).getByText("6 人/时"),
  ).toBeVisible();
  await user.click(screen.getByRole("button", { name: "风控率" }));
  expect(
    within(screen.getByRole("list", { name: "风控率对比图" })).getByText("12.5%"),
  ).toBeVisible();
});

it("切换到历史批次时显示加载状态，再展示无租约提示", async () => {
  Element.prototype.scrollIntoView = vi.fn();
  let settle: (value: ReturnType<typeof reply>) => void = () => {};
  const requested = new Promise<ReturnType<typeof reply>>((resolve) => {
    settle = resolve;
  });
  const get = vi.spyOn(fetchClient, "GET").mockImplementation(async (path, init: unknown) => {
    if (path === "/api/stats/crawl-ip-batches") {
      return reply({ items: [runningBatch, historyBatch], total: 2 });
    }
    const query = (init as { params?: { query?: { batch_id?: string } } } | undefined)?.params
      ?.query;
    return query?.batch_id === historyBatch.id ? requested : reply(crawlIpStats);
  });
  renderPanel();
  await screen.findByRole("table");
  fireEvent.keyDown(screen.getByRole("combobox", { name: "采集批次" }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("option", { name: /上周的任务/ }));
  expect(await screen.findByRole("status")).toHaveTextContent("正在加载采集 IP 数据");
  expect(screen.queryByRole("table")).toBeNull();
  await waitFor(() =>
    expect(get).toHaveBeenCalledWith(
      "/api/stats/crawl-ips",
      expect.objectContaining({
        params: { query: { batch_id: historyBatch.id } },
      }),
    ),
  );
  settle(reply(crawlIpHistory));
  expect(
    await screen.findByText("该历史批次暂无可用的采集租约明细，租约保留 24 小时。"),
  ).toBeVisible();
  expect(screen.getByRole("combobox", { name: "采集批次" })).toHaveTextContent(historyBatch.name);
});

it("初次加载失败可以重试，且空工作区有独立提示", async () => {
  let attempts = 0;
  vi.spyOn(fetchClient, "GET").mockImplementation(async (path) => {
    if (path === "/api/stats/crawl-ip-batches") return reply({ items: [], total: 0 });
    if (!attempts++) throw new Error("详细错误无需呈现");
    return reply(noCrawlIpBatches);
  });
  renderPanel();
  expect(await screen.findByRole("alert")).toHaveTextContent("采集 IP 数据加载失败");
  expect(screen.queryByText("详细错误无需呈现")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "重试" }));
  expect(await screen.findByText("还没有可查看的采集批次。")).toBeVisible();
  expect(screen.getByRole("combobox", { name: "采集批次" })).toBeDisabled();
});
