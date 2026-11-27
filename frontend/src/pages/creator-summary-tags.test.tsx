import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, expect, it } from "vitest";
import { $api, type Schemas } from "@/api/client";
import { BatchPanel } from "./batch-detail";
import { CreatorDetailPage } from "./creator-detail";

afterEach(cleanup);

const inactivityTag = "3个月内未更新";
const updatedAt = "2026-11-25T06:00:00Z";
const batch: Schemas["BatchRow"] = {
  id: "batch",
  name: "本周名单",
  source: "粘贴链接",
  status: "completed",
  ai_batch_mode: false,
  created_at: updatedAt,
  started_at: updatedAt,
  finished_at: updatedAt,
  creators: { completed: 2 },
  creator_progress: { completed: 2 },
  videos: {},
};
const creator: Schemas["CreatorSummaryView"] = {
  id: "creator",
  batch_id: batch.id,
  position: 1,
  row: null,
  name: "旅行博主",
  handle: "alice",
  oecuid: null,
  status: "completed",
  note: null,
  labels: ["图文作者"],
  tags: null,
  profile: null,
  updated_at: updatedAt,
  inactivity_tag: inactivityTag,
};
const summary: Schemas["JobView"] = {
  id: "summary",
  kind: "summary",
  run: 1,
  status: "completed",
  result: null,
  error: null,
  reused_from: null,
  artifacts_until: null,
  submitted_at: updatedAt,
  accepted_at: null,
  finished_at: updatedAt,
  last_executor_id: null,
  result_executor_id: null,
};

function queryClient() {
  return new QueryClient({ defaultOptions: { queries: { enabled: false } } });
}

function renderRoute(client: QueryClient, url: string, path: string, element: ReactElement) {
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path={path} element={element} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderCreatorDetail(
  creator: Schemas["CreatorSummaryView"],
  job: Schemas["JobView"] | null,
) {
  const client = queryClient();
  const detail: Schemas["CreatorDetail"] = {
    creator,
    batch,
    crawl: null,
    works: [],
    summary: job,
    crawl_attempts: [],
    summary_attempts: [],
    history: [],
  };
  client.setQueryData(
    $api.queryOptions("get", "/api/creators/{creator_id}", {
      params: { path: { creator_id: creator.id } },
    }).queryKey,
    detail,
  );
  renderRoute(client, `/creators/${creator.id}`, "/creators/:creatorId", <CreatorDetailPage />);
  return within(screen.getByRole("heading", { name: "汇总" }).closest("section")!);
}

it("批次内标签超过展示上限时仍显示未更新标签，并保留其他达人的原有标签", () => {
  const client = queryClient();
  const path = { batch_id: batch.id };
  const row: Schemas["CreatorRow"] = { ...creator, progress: "completed", works: [] };
  const creators: Schemas["Page_CreatorRow_"] = {
    items: [
      { ...row, tags: ["旅行", "风光", "摄影", "攻略", "徒步", "户外"] },
      { ...row, id: "fresh", position: 2, name: "近期发布达人", inactivity_tag: null },
    ],
    total: 2,
  };
  client.setQueryData(
    $api.queryOptions("get", "/api/batches/{batch_id}", { params: { path } }).queryKey,
    batch,
  );
  client.setQueryData(
    $api.queryOptions("get", "/api/batches/{batch_id}/creators", {
      params: { path, query: { page: 1, size: 50 } },
    }).queryKey,
    creators,
  );
  renderRoute(client, `/batches/${batch.id}`, "/batches/:batchId", <BatchPanel />);

  const longSummary = within(screen.getByRole("row", { name: /旅行博主/ }));
  expect(longSummary.getByText(inactivityTag)).toBeVisible();
  expect(longSummary.getByText("图文作者")).toBeVisible();
  expect(longSummary.getByText("+2")).toBeVisible();
  const recent = within(screen.getByRole("row", { name: /近期发布达人/ }));
  expect(recent.getByText("图文作者")).toBeVisible();
  expect(recent.queryByText(inactivityTag)).not.toBeInTheDocument();
});

it("达人详情的汇总合并并去重 AI 标签，名单信息继续显示原有标签", () => {
  const result = renderCreatorDetail({ ...creator, tags: ["户外", inactivityTag] }, summary);

  expect(result.getByText("户外")).toBeVisible();
  expect(result.getByText(inactivityTag)).toBeVisible();
  expect(screen.getAllByText(inactivityTag)).toHaveLength(1);
  const roster = within(screen.getByRole("heading", { name: "名单信息" }).closest("section")!);
  expect(roster.getByText("图文作者")).toBeVisible();
  expect(roster.queryByText(inactivityTag)).not.toBeInTheDocument();
});

it("达人详情无 AI 汇总任务时也显示未更新标签", () => {
  const result = renderCreatorDetail(creator, null);

  expect(result.getByText(inactivityTag)).toBeVisible();
  expect(result.getByText("图文作者")).toBeVisible();
});

it("达人详情没有汇总标签时保留原来的提示", () => {
  const result = renderCreatorDetail({ ...creator, labels: [], inactivity_tag: undefined }, null);

  expect(result.getByText("视频都有结果后自动汇总。")).toBeVisible();
  expect(result.queryByText(inactivityTag)).not.toBeInTheDocument();
});
