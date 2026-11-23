import type { AnalysisSchemas, Schemas } from "@/api/client";
import { percent } from "@/lib/format";
import { conclusions, stageNames } from "@/lib/status";
import { cacheRate, sum, type UsageRow } from "@/lib/usage";

type Stats = Schemas["Stats"];
type CountKey = keyof Schemas["Counts"];

/** 处理流程的五个环节：成功数、失败数，以及失败原因归在哪一组。 */
export const FUNNEL = [
  { label: "达人基础信息", ok: "profile_ok", failed: "profile_failed", reasons: "crawl" },
  { label: "达人视频列表", ok: "listing_ok", failed: "listing_failed", reasons: "crawl" },
  { label: "视频下载", ok: "downloaded", failed: "download_failed", reasons: "download" },
  { label: "视频 AI 识别", ok: "analyzed", failed: "analysis_failed", reasons: "analysis" },
  { label: "作者汇总", ok: "summarized", failed: "summary_failed", reasons: "summary" },
] as const satisfies readonly {
  label: string;
  ok: CountKey;
  failed: CountKey;
  reasons: Schemas["DurationStage"];
}[];

export const STAGE_LABELS: Record<string, string> = {
  crawl: "达人采集",
  download: "视频下载",
  analysis: "视频分析",
  summary: "作者汇总",
  ...stageNames,
};

export const AI_KINDS: Record<string, string> = {
  listen: "听音",
  recognize: "视频识别",
  summarize: "作者汇总",
};

/** 成功率；没有尝试时为 null。 */
export function rate(ok: number, failed: number): number | null {
  return ok + failed ? ok / (ok + failed) : null;
}

/** 一个模型在这段时间的调用；平均并发 = 调用总耗时 ÷ 经过的时长，利用率 = 平均并发 ÷ 并发上限。 */
export type ModelLoad = {
  modelId: string;
  name: string;
  kinds: string[];
  calls: number;
  failed: number;
  inputTokens: number;
  outputTokens: number;
  cacheRate: number | null;
  inFlight: number;
  capacity: number | null;
  utilization: number | null;
};

export function modelLoads(
  rows: UsageRow[],
  models: AnalysisSchemas["ModelView"][] | undefined,
  elapsedSeconds: number,
): ModelLoad[] {
  const groups = new Map<string, UsageRow[]>();
  for (const row of rows) groups.set(row.model_id, [...(groups.get(row.model_id) ?? []), row]);
  return [...groups].map(([modelId, items]) => {
    const model = models?.find((item) => item.id === modelId);
    const inFlight = elapsedSeconds > 0 ? sum(items, "busy_seconds") / elapsedSeconds : 0;
    const capacity = model?.max_concurrency ?? null;
    return {
      modelId,
      name: model?.name ?? items[0].model ?? modelId.slice(0, 8),
      kinds: [...new Set(items.map((item) => AI_KINDS[item.kind] ?? item.kind))],
      calls: sum(items, "calls"),
      failed: sum(items, "failed"),
      inputTokens: sum(items, "input_tokens"),
      outputTokens: sum(items, "output_tokens"),
      cacheRate: cacheRate(items),
      inFlight,
      capacity,
      utilization: capacity ? inFlight / capacity : null,
    };
  });
}

export type Finding = { tone: "failed" | "attention"; text: string };

/** 次数太少时比例没有参考意义。 */
const MIN_COUNT = 5;

/**
 * 从统计里找出需要人看一眼的地方：成功率低的环节和最常见的原因、无法采集的达人偏多、
 * 重试偏多、缓存命中低、模型并发用满，以及当前待处理的问题达人和视频。失败的排在前面。
 */
export function findings(stats: Stats, usage: UsageRow[], loads: ModelLoad[]): Finding[] {
  const { totals } = stats;
  const found: Finding[] = [];

  for (const step of FUNNEL) {
    const ok = totals[step.ok];
    const failed = totals[step.failed];
    const value = rate(ok, failed);
    if (value === null || ok + failed < MIN_COUNT || value >= 0.9) continue;
    const top = stats.failures.find((row) => row.stage === step.reasons);
    const reason = top ? `，最多的原因：${top.reason}（${top.count} 次）` : "";
    found.push({
      tone: value < 0.7 ? "failed" : "attention",
      text: `${step.label}成功率 ${percent(value)}（失败 ${failed}）${reason}`,
    });
  }

  if (totals.profile_ok >= MIN_COUNT && totals.unreachable / totals.profile_ok >= 0.2) {
    const parts = (["private", "login_required", "not_found"] as const)
      .filter((key) => stats.conclusions[key])
      .map((key) => `${conclusions[key]} ${stats.conclusions[key]}`);
    found.push({
      tone: "attention",
      text: `${percent(totals.unreachable / totals.profile_ok)} 的达人无法采集（${parts.join("、")}），检查名单或账号登录状态`,
    });
  }

  for (const stage of stats.stages) {
    const done = stage.succeeded + stage.failed;
    if (stage.retried >= MIN_COUNT && stage.retried >= done * 0.2) {
      found.push({
        tone: "attention",
        text: `${STAGE_LABELS[stage.stage] ?? stage.stage}重试偏多：完成 ${done} 次、重试 ${stage.retried} 次，可能被限流或网络不稳`,
      });
    }
  }

  const cache = cacheRate(usage);
  if (cache !== null && cache < 0.2 && sum(usage, "calls") >= MIN_COUNT) {
    found.push({
      tone: "attention",
      text: `AI 缓存命中率只有 ${percent(cache)}：确认模型开启了显式缓存，提示词不少于 1024 token`,
    });
  }

  for (const load of loads) {
    if (load.utilization !== null && load.utilization >= 0.9) {
      found.push({
        tone: "attention",
        text: `模型 ${load.name} 并发基本用满（平均 ${load.inFlight.toFixed(1)} / ${load.capacity}），任务在排队，可以调高并发上限`,
      });
    }
  }

  const { creators, videos } = stats;
  if (creators.recrawl) {
    found.push({
      tone: "attention",
      text: `${creators.recrawl} 位达人需要重新抓取，可在批次里点“重新抓取”`,
    });
  }
  if (videos.failed) {
    found.push({
      tone: "failed",
      text: `${videos.failed} 个视频分析失败，可在批次里点“补跑失败”`,
    });
  }
  if (videos.needs_review) {
    found.push({ tone: "attention", text: `${videos.needs_review} 个视频待核查` });
  }

  return found.sort((a, b) => Number(b.tone === "failed") - Number(a.tone === "failed"));
}
