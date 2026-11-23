import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import { $ai, $api, type AnalysisSchemas, errorText, type Schemas } from "@/api/client";
import { Notice } from "@/components/page";
import { Legend, SegmentBar } from "@/components/status";
import { bytes, compact, percent, seconds } from "@/lib/format";
import {
  conclusions,
  creatorProgress,
  ordered,
  type Tone,
  toneClass,
  workStatus,
} from "@/lib/status";
import { cn } from "@/lib/utils";
import { type Finding, type ModelLoad, rate, STAGE_LABELS } from "./insights";

type Conclusion = Schemas["CrawlConclusion"];

const CONCLUSION_TONES: Record<Conclusion, Tone> = {
  ok: "done",
  private: "muted",
  login_required: "attention",
  not_found: "idle",
  failed: "failed",
};
const FAILURE_STAGES: Schemas["DurationStage"][] = ["crawl", "download", "analysis", "summary"];

export function FindingList({ items }: { items: Finding[] }) {
  if (!items.length) {
    return (
      <Notice icon={<CheckCircle2 className="text-status-done size-4" />}>
        各环节正常，没有需要处理的问题
      </Notice>
    );
  }
  return (
    <ul className="divide-y">
      {items.map((item) => {
        const Icon = item.tone === "failed" ? XCircle : AlertTriangle;
        return (
          <li key={item.text} className="flex gap-2.5 px-4 py-2.5 text-sm">
            <Icon className={cn("mt-0.5 size-4 shrink-0", toneClass[item.tone].text)} />
            {item.text}
          </li>
        );
      })}
    </ul>
  );
}

/** 爬取结论的占比：高的分段条加图例。 */
export function Conclusions({ counts }: { counts: Schemas["Stats"]["conclusions"] }) {
  const segments = (Object.keys(conclusions) as Conclusion[])
    .filter((key) => counts[key])
    .map((key) => ({
      status: key,
      label: conclusions[key],
      tone: CONCLUSION_TONES[key],
      count: counts[key],
    }));
  if (!segments.length) return <Notice>这段时间没有爬取结论</Notice>;
  return (
    <div className="space-y-3 p-4">
      <SegmentBar segments={segments} className="h-6 gap-0.5 rounded-md bg-transparent" />
      <Legend segments={segments} />
    </div>
  );
}

/** 统计范围内的批次现在各阶段的达人和视频。 */
export function Snapshot({ stats }: { stats: Schemas["Stats"] }) {
  return (
    <div className="space-y-5 p-4">
      <Distribution title="达人" segments={ordered(creatorProgress, stats.creators)} />
      <Distribution title="视频" segments={ordered(workStatus, stats.videos)} />
    </div>
  );
}

function Distribution({
  title,
  segments,
}: {
  title: string;
  segments: ReturnType<typeof ordered>;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between text-xs">
        <span className="font-medium">{title}</span>
        <span className="text-muted-foreground font-mono">
          共 {segments.reduce((sum, item) => sum + item.count, 0)}
        </span>
      </div>
      <SegmentBar segments={segments} className="h-2" />
      <Legend segments={segments} />
    </div>
  );
}

/** 此刻各环节的并发占用：采集、下载看在线爬取设备，AI 看各模型。 */
export function LiveLoad() {
  const options = { refetchInterval: 5_000, retry: false } as const;
  const executors = $api.useQuery("get", "/api/executors", {}, options);
  const crawls = $api.useQuery("get", "/api/executors/crawler/crawl-progress", {}, options);
  const downloads = $api.useQuery("get", "/api/executors/crawler/download-progress", {}, options);
  const analysis = $api.useQuery(
    "get",
    "/api/executors/{kind}/status",
    { params: { path: { kind: "analysis" } } },
    options,
  );
  const models = $ai.useQuery("get", "/api/ai/models");

  const crawlers = executors.data?.filter((item) => item.kind === "crawler" && item.online) ?? [];
  const slots = (key: "crawl_slots" | "download_slots") =>
    crawlers.reduce((sum, item) => sum + item[key], 0);
  const active = crawls.data?.flatMap((item) => item.crawls ?? []) ?? [];
  const waiting = active.filter((task) => task.stage === "waiting_line").length;
  const transfers = downloads.data?.flatMap((item) => item.downloads ?? []) ?? [];
  const speed =
    downloads.data?.reduce((sum, item) => sum + (item.download_bytes_per_second ?? 0), 0) ?? 0;
  const status = analysis.data as AnalysisSchemas["ServiceStatus"] | undefined;
  const queued = Object.entries(status?.ready ?? {}).filter(([, count]) => count);
  const modelName = (id: string) =>
    models.data?.find((model) => model.id === id)?.name ?? id.slice(0, 8);

  return (
    <div className="space-y-4 p-4">
      <Meter
        label="达人采集"
        used={active.length}
        capacity={slots("crawl_slots")}
        note={waiting ? `其中 ${waiting} 个在等代理线路` : undefined}
      />
      <Meter
        label="视频下载"
        used={transfers.length}
        capacity={slots("download_slots")}
        note={speed ? `${bytes(speed)}/秒` : undefined}
      />
      {status?.models.map((model) => (
        <Meter
          key={model.model_id}
          label={`AI · ${modelName(model.model_id)}`}
          used={model.running}
          capacity={model.capacity}
          note={`近一分钟 ${model.recent_requests} / ${model.requests_per_minute} 次`}
        />
      ))}
      <p className="text-muted-foreground text-xs">
        {analysis.error
          ? `分析服务：${errorText(analysis.error)}`
          : queued.length
            ? `分析排队：${queued.map(([stage, count]) => `${STAGE_LABELS[stage] ?? stage} ${count}`).join(" · ")}`
            : "分析服务没有排队的任务"}
      </p>
    </div>
  );
}

function Meter({
  label,
  used,
  capacity,
  note,
}: {
  label: string;
  used: number;
  capacity: number;
  note?: string;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="font-medium">{label}</span>
        <span className="text-muted-foreground font-mono">
          {capacity ? `${used} / ${capacity} · ${percent(used / capacity, 0)}` : "没有在线的并发"}
        </span>
      </div>
      <div className="bg-muted h-1.5 overflow-hidden rounded-full">
        <div
          className="bg-status-active h-full rounded-full transition-[width] duration-500"
          style={{ width: `${capacity ? Math.min(used / capacity, 1) * 100 : 0}%` }}
        />
      </div>
      {note && <div className="text-muted-foreground text-xs">{note}</div>}
    </div>
  );
}

export function StageTable({ rows }: { rows: Schemas["StageRow"][] }) {
  if (!rows.length) return <Notice>这段时间没有分析记录</Notice>;
  return (
    <Grid head={["阶段", "成功", "失败", "重试", "跳过", "成功率"]}>
      {rows.map((row) => {
        const value = rate(row.succeeded, row.failed);
        return (
          <tr key={row.stage} className="border-t">
            <td className="px-4 py-2 font-sans">{STAGE_LABELS[row.stage] ?? row.stage}</td>
            <td className="px-4 py-2 text-right">{row.succeeded}</td>
            <td className={cn("px-4 py-2 text-right", row.failed && "text-status-failed")}>
              {row.failed}
            </td>
            <td className="px-4 py-2 text-right">{row.retried}</td>
            <td className="px-4 py-2 text-right">{row.skipped}</td>
            <td className="px-4 py-2">
              <span className="flex items-center justify-end gap-2">
                <span className="bg-muted flex h-1.5 w-16 overflow-hidden rounded-full">
                  <span
                    className="bg-status-done h-full"
                    style={{ width: `${(value ?? 0) * 100}%` }}
                  />
                  {value !== null && <span className="bg-status-failed h-full flex-1" />}
                </span>
                <span className="w-12 text-right">{percent(value)}</span>
              </span>
            </td>
          </tr>
        );
      })}
    </Grid>
  );
}

export function DurationTable({ rows }: { rows: Schemas["DurationRow"][] }) {
  if (!rows.length) return <Notice>这段时间没有完成的任务</Notice>;
  return (
    <Grid head={["环节", "完成", "平均", "中位数", "最慢 10%"]}>
      {rows.map((row) => (
        <tr key={row.stage} className="border-t">
          <td className="px-4 py-2 font-sans">{STAGE_LABELS[row.stage] ?? row.stage}</td>
          <td className="px-4 py-2 text-right">{row.count}</td>
          <td className="px-4 py-2 text-right">{seconds(row.average)}</td>
          <td className="px-4 py-2 text-right">{seconds(row.median)}</td>
          <td className="px-4 py-2 text-right">{seconds(row.p90)}</td>
        </tr>
      ))}
    </Grid>
  );
}

/** 各环节最常见的失败原因，按次数排列。 */
export function FailureList({ rows }: { rows: Schemas["FailureRow"][] }) {
  if (!rows.length) {
    return (
      <Notice icon={<CheckCircle2 className="text-status-done size-4" />}>这段时间没有失败</Notice>
    );
  }
  const max = Math.max(...rows.map((row) => row.count));
  return (
    <div className="space-y-4 p-4">
      {FAILURE_STAGES.map((stage) => {
        const items = rows.filter((row) => row.stage === stage);
        return (
          items.length > 0 && (
            <div key={stage} className="space-y-1.5">
              <h3 className="text-muted-foreground text-xs">{STAGE_LABELS[stage]}</h3>
              {items.map((row) => (
                <div
                  key={row.reason}
                  className="grid grid-cols-[minmax(0,1fr)_96px_40px] items-center gap-3 text-xs"
                >
                  <span className="truncate" title={row.reason}>
                    {row.reason}
                  </span>
                  <span className="bg-muted h-1.5 overflow-hidden rounded-full">
                    <span
                      className="bg-status-failed/70 block h-full rounded-full"
                      style={{ width: `${(row.count / max) * 100}%` }}
                    />
                  </span>
                  <span className="text-right font-mono">{row.count}</span>
                </div>
              ))}
            </div>
          )
        );
      })}
    </div>
  );
}

export function AiTable({ loads, error }: { loads: ModelLoad[]; error: unknown }) {
  if (error) return <Notice>分析服务：{errorText(error)}</Notice>;
  if (!loads.length) return <Notice>这段时间没有 AI 调用</Notice>;
  return (
    <Grid
      head={[
        "模型",
        "用途",
        "调用",
        "失败",
        "输入 token",
        "输出 token",
        "缓存命中",
        "平均并发",
        "利用率",
      ]}
    >
      {loads.map((load) => (
        <tr key={load.modelId} className="border-t">
          <td className="px-4 py-2 font-sans">{load.name}</td>
          <td className="text-muted-foreground px-4 py-2 font-sans">{load.kinds.join("、")}</td>
          <td className="px-4 py-2 text-right">{compact(load.calls)}</td>
          <td className={cn("px-4 py-2 text-right", load.failed && "text-status-failed")}>
            {load.failed}
          </td>
          <td className="px-4 py-2 text-right">{compact(load.inputTokens)}</td>
          <td className="px-4 py-2 text-right">{compact(load.outputTokens)}</td>
          <td className="px-4 py-2 text-right">{percent(load.cacheRate)}</td>
          <td className="px-4 py-2 text-right">
            {load.inFlight.toFixed(1)}
            {load.capacity && <span className="text-muted-foreground"> / {load.capacity}</span>}
          </td>
          <td className="px-4 py-2 text-right">{percent(load.utilization)}</td>
        </tr>
      ))}
    </Grid>
  );
}

/** 数字表格：第一列是名称，其余右对齐。 */
function Grid({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-muted-foreground text-xs">
          <tr>
            {head.map((label, index) => (
              <th
                key={label}
                className={cn(
                  "px-4 py-2 font-normal whitespace-nowrap",
                  index ? "text-right" : "text-left",
                )}
              >
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="font-mono [&_td]:whitespace-nowrap">{children}</tbody>
      </table>
    </div>
  );
}
