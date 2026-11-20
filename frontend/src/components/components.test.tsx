import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Facts, QueryState, Tags } from "@/components/page";
import { StatusOf } from "@/components/status";
import { workStatus } from "@/lib/status";

describe("QueryState", () => {
  const content = (data: string[]) => <span>{data.join(",")}</span>;

  it("出错时显示后端的错误信息", () => {
    const query = { data: undefined, error: { detail: "分析服务不在线" }, isPending: false };
    render(<QueryState query={query}>{content}</QueryState>);
    expect(screen.getByText("分析服务不在线")).toBeInTheDocument();
  });

  it("空数据显示空状态", () => {
    const query = { data: [], error: null, isPending: false };
    render(
      <QueryState query={query} isEmpty={(data) => !data.length} empty="没有批次">
        {content}
      </QueryState>,
    );
    expect(screen.getByText("没有批次")).toBeInTheDocument();
  });

  it("有数据时渲染内容", () => {
    const query = { data: ["甲", "乙"], error: null, isPending: false };
    render(<QueryState query={query}>{content}</QueryState>);
    expect(screen.getByText("甲,乙")).toBeInTheDocument();
  });
});

describe("展示组件", () => {
  it("状态按状态表显示中文", () => {
    render(<StatusOf labels={workStatus} value="needs_review" />);
    expect(screen.getByText("待核查")).toBeInTheDocument();
  });

  it("标签超过上限时显示剩余数量", () => {
    render(<Tags tags={["连衣裙", "女装", "夏季", "通勤"]} max={2} />);
    expect(screen.getByText("+2")).toBeInTheDocument();
    expect(screen.queryByText("夏季")).not.toBeInTheDocument();
  });

  it("键值列表的空值显示占位符", () => {
    render(<Facts items={[["原因", null]]} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
