import { describe, expect, it } from "vitest";
import type { Schemas } from "@/api/client";
import type { UsageRow } from "@/lib/usage";
import { findings, modelLoads } from "./insights";
import { defaultBucket, hourly, periodOf } from "./period";

const NOW = new Date(2026, 10, 23, 14, 25);

function stats(changes: Partial<Schemas["Stats"]> = {}): Schemas["Stats"] {
  const totals = {
    profile_ok: 100,
    profile_failed: 0,
    listing_ok: 90,
    listing_failed: 0,
    unreachable: 0,
    videos_found: 900,
    downloaded: 900,
    downloaded_bytes: 0,
    download_failed: 0,
    analyzed: 900,
    reused: 0,
    analysis_failed: 0,
    summarized: 90,
    summary_failed: 0,
  };
  return {
    since: "",
    until: "",
    bucket: "hour",
    batch_id: null,
    totals,
    conclusions: {},
    timeline: [],
    stages: [],
    durations: [],
    failures: [],
    creators: {},
    videos: {},
    ...changes,
  };
}

function usage(changes: Partial<UsageRow>): UsageRow {
  return {
    at: "",
    kind: "recognize",
    model_id: "m1",
    model: "qwen3.6-plus",
    calls: 10,
    succeeded: 10,
    failed: 0,
    input_tokens: 10_000,
    output_tokens: 500,
    cached_tokens: 8_000,
    cache_input_tokens: 10_000,
    busy_seconds: 0,
    ...changes,
  };
}

describe("时间段", () => {
  it("预设对齐到整点或零点，同一小时内不变", () => {
    expect(periodOf("day", NOW)).toEqual({
      since: new Date(2026, 10, 22, 15),
      until: new Date(2026, 10, 23, 15),
    });
    expect(periodOf("today", NOW)).toEqual({
      since: new Date(2026, 10, 23),
      until: new Date(2026, 10, 24),
    });
    expect(periodOf("week", NOW)?.since).toEqual(new Date(2026, 10, 17));
  });

  it("批次全程从开始的整点到结束后的整点，没开始时没有时间段", () => {
    const batch = {
      started_at: new Date(2026, 10, 20, 9, 40).toISOString(),
      finished_at: null,
    } as Schemas["BatchRow"];
    expect(periodOf("batch", NOW, { batch })).toEqual({
      since: new Date(2026, 10, 20, 9),
      until: new Date(2026, 10, 23, 15),
    });
    expect(periodOf("batch", NOW, { batch: { ...batch, started_at: null } })).toBeNull();
  });

  it("自定义时间段不完整或倒置时没有时间段", () => {
    expect(periodOf("custom", NOW, { from: "2026-11-20T08:00" })).toBeNull();
    expect(
      periodOf("custom", NOW, { from: "2026-11-21T08:00", to: "2026-11-20T08:00" }),
    ).toBeNull();
  });

  it("三天以内默认按小时，太长时只能按天", () => {
    expect(defaultBucket(periodOf("week", NOW)!)).toBe("day");
    expect(defaultBucket(periodOf("day", NOW)!)).toBe("hour");
    expect(hourly(periodOf("month", NOW)!)).toBe(true);
    const long = { since: new Date(2026, 0, 1), until: NOW };
    expect(hourly(long)).toBe(false);
  });
});

describe("需要关注", () => {
  it("各环节正常时没有提示", () => {
    expect(findings(stats(), [usage({})], [])).toEqual([]);
  });

  it("成功率低的环节带上最多的失败原因，失败的排在前面", () => {
    const data = stats({
      totals: { ...stats().totals, downloaded: 50, download_failed: 50, listing_failed: 15 },
      failures: [{ stage: "download", reason: "连接超时", count: 30 }],
      videos: { needs_review: 2 },
    });
    expect(findings(data, [], [])).toEqual([
      {
        tone: "failed",
        text: "视频下载成功率 50.0%（失败 50），最多的原因：连接超时（30 次）",
      },
      { tone: "attention", text: "达人视频列表成功率 85.7%（失败 15）" },
      { tone: "attention", text: "2 个视频待核查" },
    ]);
  });

  it("次数太少时不看成功率", () => {
    const data = stats({ totals: { ...stats().totals, summarized: 1, summary_failed: 2 } });
    expect(findings(data, [], [])).toEqual([]);
  });

  it("缓存命中低、模型并发用满时提示", () => {
    const rows = [usage({ cached_tokens: 500, busy_seconds: 3600 * 9.5 })];
    const loads = modelLoads(
      rows,
      [{ id: "m1", name: "识别模型", max_concurrency: 10 } as never],
      3600,
    );
    expect(loads[0]).toMatchObject({ name: "识别模型", kinds: ["视频识别"], utilization: 0.95 });
    expect(findings(stats(), rows, loads).map((item) => item.text)).toEqual([
      "AI 缓存命中率只有 5.0%：确认模型开启了显式缓存，提示词不少于 1024 token",
      "模型 识别模型 并发基本用满（平均 9.5 / 10），任务在排队，可以调高并发上限",
    ]);
  });
});
