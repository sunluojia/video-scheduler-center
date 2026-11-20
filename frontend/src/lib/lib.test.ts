import { describe, expect, it } from "vitest";
import { errorText } from "@/api/client";
import { diffLines } from "@/lib/diff";
import { bytes, duration } from "@/lib/format";
import { creatorStatus, ordered, total } from "@/lib/status";

describe("状态计数", () => {
  it("按状态表顺序排列，并略过数量为零的状态", () => {
    const segments = ordered(creatorStatus, { completed: 3, pending: 2, failed: 0 });
    expect(segments.map((segment) => segment.status)).toEqual(["pending", "completed"]);
    expect(segments[1]).toMatchObject({ label: "已完成", tone: "done", count: 3 });
  });

  it("合计忽略缺失的状态", () => {
    expect(total({ completed: 3, failed: undefined, analyzing: 2 })).toBe(5);
  });
});

describe("格式化", () => {
  it("字节数换算到合适的单位", () => {
    expect(bytes(512)).toBe("512 B");
    expect(bytes(1536)).toBe("1.5 KB");
    expect(bytes(5 * 1024 ** 3)).toBe("5.0 GB");
  });

  it("时长按大小选择单位", () => {
    expect(duration("2026-01-01T00:00:00Z", "2026-01-01T00:00:42Z")).toBe("42 秒");
    expect(duration("2026-01-01T00:00:00Z", "2026-01-01T01:30:00Z")).toBe("1 小时 30 分");
    expect(duration(null)).toBe("—");
  });

  it("错误信息取后端的 detail", () => {
    expect(errorText({ detail: "批次已取消" })).toBe("批次已取消");
    expect(errorText({ detail: [{ msg: "字段缺失" }, { msg: "格式不对" }] })).toBe(
      "字段缺失；格式不对",
    );
    expect(errorText(undefined)).toBe("请求失败，请稍后重试");
  });
});

describe("按行对比", () => {
  it("标出新增和删除的行", () => {
    expect(diffLines("甲\n乙\n丙", "甲\n丁\n丙")).toEqual([
      { kind: "same", text: "甲" },
      { kind: "removed", text: "乙" },
      { kind: "added", text: "丁" },
      { kind: "same", text: "丙" },
    ]);
  });

  it("相同文本全部是未改动", () => {
    expect(diffLines("a\nb", "a\nb").every((line) => line.kind === "same")).toBe(true);
  });
});
