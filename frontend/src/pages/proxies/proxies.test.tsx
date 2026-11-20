import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { UseBoard } from "./board";
import { address, depths, type Folder, subtree, type Tag, type UseCard } from "./data";
import { UsePicker } from "./shared";

afterEach(cleanup);

const uses = [
  { key: "crawler.crawl_creator", service: "crawler", service_name: "爬取程序", name: "获取信息" },
  { key: "crawler.download_video", service: "crawler", service_name: "爬取程序", name: "下载视频" },
] as UseCard[];

it.each([0, 1])("旧端口只有在任务进行中时才显示（使用中 %i）", (inUse) => {
  const rule: UseCard["rule"] = {
    source: "pool",
    folder_id: null,
    local_port: null,
    mode: "priority",
    line_limit: 2,
    updated_at: "2026-01-01T00:00:00Z",
  };
  const pool: UseCard["lines"][number] = {
    key: "203.0.113.1",
    kind: "pool",
    device: null,
    exit_ip: "203.0.113.1",
    proxies: ["动态代理"],
    state: "open",
    score: 0,
    cooldown_until: null,
    reason: null,
    in_use: 0,
    limit: 2,
    recent_ok: 0,
    recent_failed: 0,
    tested_at: null,
  };
  const formerPort = {
    ...pool,
    key: "port:crawler-1:11479",
    kind: "port" as const,
    device: "crawler-1",
    exit_ip: null,
    proxies: [],
    in_use: inUse,
    recent_ok: 3,
  };
  const currentPort = { ...formerPort, key: "port:crawler-1:11480", in_use: 0, recent_ok: 0 };
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
  client.setQueryData(["get", "/api/proxy-folders"], { folders: [], unfiled: 1 });
  client.setQueryData<UseCard[]>(
    ["get", "/api/proxy-uses", {}],
    [
      {
        ...uses[0],
        rule,
        proxies: 1,
        lines: [
          pool,
          { ...currentPort, key: "direct:old-crawler", kind: "direct", device: "old-crawler" },
          formerPort,
          currentPort,
        ],
        devices: [
          {
            instance_id: "crawler-1",
            online: true,
            last_seen_at: null,
            rule: { ...rule, source: "local_port", local_port: 11480 },
            in_use: inUse,
            line: currentPort,
          },
        ],
      },
    ],
  );

  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <TooltipProvider>
          <UseBoard />
        </TooltipProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );

  expect(screen.getByRole("button", { name: "线路 203.0.113.1" })).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "线路 本机端口 11480 · crawler-1" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /线路 直连/ })).toBeNull();
  expect(screen.queryAllByRole("button", { name: /线路 本机端口 11479/ })).toHaveLength(inUse);
  expect(screen.getByText(/^线路$/)).toHaveTextContent(`线路 ${2 + inUse}`);
  expect(screen.getByTitle("最近一小时：正常 / 异常")).toHaveTextContent("3 / 0");
});

function Picker() {
  const [value, setValue] = useState<Tag[]>([]);
  return (
    <>
      <UsePicker value={value} onChange={setValue} />
      <output>{value.join(",")}</output>
    </>
  );
}

describe("UsePicker", () => {
  it("选整个服务时去掉单个任务，任务按钮跟着亮起且不能单独点", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
    client.setQueryData(["get", "/api/proxy-uses", {}], uses);
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={client}>
        <Picker />
      </QueryClientProvider>,
    );
    const value = () => screen.getByRole("status").textContent;
    const download = screen.getByRole("button", { name: "下载视频" });

    await user.click(download);
    expect(value()).toBe("crawler.download_video");

    await user.click(screen.getByRole("button", { name: "全部任务" }));
    expect(value()).toBe("crawler");
    expect(download).toBeDisabled();
    expect(download).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "全部任务" }));
    expect(value()).toBe("");
  });
});

describe("文件夹树", () => {
  // 接口按树的先后顺序返回：父文件夹总在子文件夹前面
  const folders = [
    { id: "us", name: "美国", parent_id: null, count: 1 },
    { id: "la", name: "洛杉矶", parent_id: "us", count: 2 },
    { id: "la-1", name: "一号机房", parent_id: "la", count: 0 },
    { id: "jp", name: "日本", parent_id: null, count: 3 },
  ] satisfies Folder[];

  it("算出每个文件夹的层级", () => {
    expect([...depths(folders)]).toEqual([
      ["us", 0],
      ["la", 1],
      ["la-1", 2],
      ["jp", 0],
    ]);
  });

  it("子树包括自己和所有下级", () => {
    expect([...subtree(folders, "us")]).toEqual(["us", "la", "la-1"]);
    expect([...subtree(folders, "jp")]).toEqual(["jp"]);
  });
});

describe("address", () => {
  it("IPv6 主机加方括号", () => {
    expect(address("1.2.3.4", 80)).toBe("1.2.3.4:80");
    expect(address("2001:db8::1", 1080)).toBe("[2001:db8::1]:1080");
  });
});
