import { keepPreviousData } from "@tanstack/react-query";
import { format } from "date-fns";
import { Download } from "lucide-react";
import { $ai, $api, type AnalysisSchemas, type Schemas } from "@/api/client";
import { Choice } from "@/components/choice";
import { Notice, PageHeader, Panel, QueryState, Section } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { bytes, compact, percent } from "@/lib/format";
import { useFilters } from "@/lib/params";
import { type Tone, toneClass } from "@/lib/status";
import { cacheRate, sum, type UsageRow } from "@/lib/usage";
import { AiTrend, Trend } from "./charts";
import { CrawlIpPanel } from "./crawl-ips";
import { FUNNEL, findings, modelLoads, rate } from "./insights";
import {
  AiTable,
  Conclusions,
  DurationTable,
  FailureList,
  FindingList,
  LiveLoad,
  Snapshot,
  StageTable,
} from "./panels";
import {
  type Bucket,
  defaultBucket,
  hourly,
  inputValue,
  type Period,
  periodOf,
  type Preset,
  PRESETS,
} from "./period";

const BUCKETS: Record<Bucket, string> = { hour: "按小时", day: "按天" };
const REFRESH = { placeholderData: keepPreviousData, refetchInterval: 60_000 } as const;

type Stats = Schemas["Stats"];
type Usage = { data: UsageRow[] | undefined; error: unknown; dataUpdatedAt: number };

/**
 * 数据看板：一段时间里各环节做了多少、成功率、用时、失败原因和 AI 用量，以及此刻的负载。
 * 时间段、粒度和批次写在地址栏里，导出的 Excel 和页面是同一份统计。
 */
export function StatsPage() {
  const { values, set } = useFilters(["range", "from", "to", "bucket", "batch"] as const);
  const batches = $api.useQuery("get", "/api/batches", { params: { query: { size: 200 } } });
  const batch = batches.data?.items.find((item) => item.id === values.batch);
  const preset: Preset =
    values.range && Object.hasOwn(PRESETS, values.range)
      ? (values.range as Preset)
      : values.batch
        ? "batch"
        : "day";
  const period = periodOf(preset, new Date(), { from: values.from, to: values.to, batch });
  const bucket: Bucket =
    period && !hourly(period)
      ? "day"
      : values.bucket === "hour" || values.bucket === "day"
        ? values.bucket
        : period
          ? defaultBucket(period)
          : "hour";
  const query = period && {
    since: period.since.toISOString(),
    until: period.until.toISOString(),
    bucket,
    ...(values.batch && { batch_id: values.batch }),
  };
  const init = { params: { query: query ?? { since: "", until: "" } } };
  const stats = $api.useQuery("get", "/api/stats", init, { ...REFRESH, enabled: !!query });
  const usage = $ai.useQuery("get", "/api/ai/usage", init, {
    ...REFRESH,
    enabled: !!query,
    retry: false,
  });
  const models = $ai.useQuery("get", "/api/ai/models", {}, { retry: false });

  const choose = (range: Preset) =>
    set(
      range === "custom" && period
        ? { range, from: inputValue(period.since), to: inputValue(period.until), bucket: undefined }
        : { range, from: undefined, to: undefined, bucket: undefined },
    );

  return (
    <>
      <PageHeader
        eyebrow="Statistics"
        title="数据看板"
        meta={
          period && (
            <>
              <span>
                {format(period.since, "MM-dd HH:mm")} 至 {format(period.until, "MM-dd HH:mm")}
              </span>
              <span>{batch ? batch.name : "全部批次"}</span>
              {stats.dataUpdatedAt > 0 && (
                <span>每分钟刷新 · 更新于 {format(stats.dataUpdatedAt, "HH:mm:ss")}</span>
              )}
            </>
          )
        }
        actions={
          query && (
            <Button size="sm" variant="outline" asChild>
              <a href={`/api/stats/export?${new URLSearchParams(query)}`}>
                <Download /> 导出 Excel
              </a>
            </Button>
          )
        }
      />
      <Panel className="rise flex flex-wrap items-center gap-3 p-3 [&>[role=group]]:flex-wrap">
        <Choice
          label="时间段"
          options={PRESETS}
          value={preset}
          onChange={choose}
          disabled={values.batch ? [] : ["batch"]}
        />
        {preset === "custom" && (
          <span className="flex min-w-0 flex-wrap items-center gap-2 text-xs">
            <Input
              type="datetime-local"
              aria-label="开始时间"
              className="h-8 w-full sm:w-48"
              value={values.from ?? ""}
              onChange={(event) => set({ from: event.target.value })}
            />
            至
            <Input
              type="datetime-local"
              aria-label="结束时间"
              className="h-8 w-full sm:w-48"
              value={values.to ?? ""}
              onChange={(event) => set({ to: event.target.value })}
            />
          </span>
        )}
        <Choice
          label="时段粒度"
          options={BUCKETS}
          value={bucket}
          onChange={(value) => set({ bucket: value })}
          disabled={period && !hourly(period) ? ["hour"] : []}
        />
        <Select
          value={values.batch ?? "all"}
          onValueChange={(value) =>
            set({
              batch: value === "all" ? undefined : value,
              range: undefined,
              from: undefined,
              to: undefined,
              bucket: undefined,
            })
          }
        >
          <SelectTrigger size="sm" className="w-full sm:ml-auto sm:w-64" aria-label="批次">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部批次</SelectItem>
            {batches.data?.items
              .filter((item) => item.status !== "draft")
              .map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.name}
                </SelectItem>
              ))}
          </SelectContent>
        </Select>
      </Panel>
      {period ? (
        <QueryState query={stats} rows={8}>
          {(data) => <Board stats={data} usage={usage} models={models.data} period={period} />}
        </QueryState>
      ) : (
        <Panel>
          <Notice>{preset === "custom" ? "选择开始和结束时间" : "这个批次还没开始"}</Notice>
        </Panel>
      )}
      <CrawlIpPanel />
    </>
  );
}

function Board({
  stats,
  usage,
  models,
  period,
}: {
  stats: Stats;
  usage: Usage;
  models: AnalysisSchemas["ModelView"][] | undefined;
  period: Period;
}) {
  const rows = usage.data ?? [];
  // 平均并发按取数时已经过去的时长算
  const elapsed =
    (Math.min(period.until.getTime(), usage.dataUpdatedAt) - period.since.getTime()) / 1000;
  const loads = modelLoads(rows, models, elapsed);
  const { totals } = stats;
  const notes: Partial<Record<(typeof FUNNEL)[number]["ok"], string>> = {
    profile_ok: `无法采集 ${totals.unreachable} 位`,
    listing_ok: `锁定视频 ${compact(totals.videos_found)} 个`,
    downloaded: `共 ${bytes(totals.downloaded_bytes)}`,
    analyzed: `复用已有结果 ${compact(totals.reused)} 个`,
  };
  return (
    <>
      <div className="rise grid gap-3 sm:grid-cols-3 xl:grid-cols-6">
        {FUNNEL.map((step) => (
          <StepTile
            key={step.ok}
            label={step.label}
            ok={totals[step.ok]}
            failed={totals[step.failed]}
            note={notes[step.ok]}
          />
        ))}
        <CacheTile usage={usage} />
      </div>
      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-[minmax(0,1fr)_400px]">
        <Section title="分时趋势">
          <Panel>
            <Trend stats={stats} />
          </Panel>
        </Section>
        <div className="space-y-5">
          <Section title="需要关注">
            <Panel>
              <FindingList items={findings(stats, rows, loads)} />
            </Panel>
          </Section>
          <Section title="达人爬取结论">
            <Panel>
              <Conclusions counts={stats.conclusions} />
            </Panel>
          </Section>
        </div>
      </div>
      <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-2">
        <Section title="实时负载" extra={<Hint>此刻的并发占用，每 5 秒刷新</Hint>}>
          <Panel>
            <LiveLoad />
          </Panel>
        </Section>
        <Section title="当前进度" extra={<Hint>统计范围内的批次现在的状态</Hint>}>
          <Panel>
            <Snapshot stats={stats} />
          </Panel>
        </Section>
      </div>
      <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-2">
        <Section title="AI 调用趋势">
          <Panel>
            {usage.error ? (
              <AiTable loads={[]} error={usage.error} />
            ) : (
              <AiTrend timeline={stats.timeline} usage={rows} bucket={stats.bucket} />
            )}
          </Panel>
        </Section>
        <Section title="分析各阶段" extra={<Hint>听音按音频段计数</Hint>}>
          <Panel>
            <StageTable rows={stats.stages} />
          </Panel>
        </Section>
      </div>
      <Section
        title="AI 模型用量"
        extra={<Hint>利用率 = 平均并发 ÷ 并发上限，长期接近 100% 说明任务在排队</Hint>}
      >
        <Panel>
          <AiTable loads={loads} error={usage.error} />
        </Panel>
      </Section>
      <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-2">
        <Section title="用时" extra={<Hint>这段时间里做完的任务</Hint>}>
          <Panel>
            <DurationTable rows={stats.durations} />
          </Panel>
        </Section>
        <Section title="失败原因" extra={<Hint>多次尝试的只算最后一次的原因</Hint>}>
          <Panel>
            <FailureList rows={stats.failures} />
          </Panel>
        </Section>
      </div>
    </>
  );
}

function Hint({ children }: { children: string }) {
  return <span className="text-muted-foreground text-xs">{children}</span>;
}

/** 一个环节的成功数，下方是成功和失败的比例条。 */
function StepTile({
  label,
  ok,
  failed,
  note,
}: {
  label: string;
  ok: number;
  failed: number;
  note?: string;
}) {
  const value = rate(ok, failed);
  const tone: Tone =
    value === null ? "muted" : value >= 0.9 ? "done" : value >= 0.7 ? "attention" : "failed";
  return (
    <Panel className="space-y-2 p-4">
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="font-mono text-2xl font-semibold tabular-nums">{compact(ok)}</div>
      <div className="bg-muted flex h-1.5 gap-0.5 overflow-hidden rounded-full">
        {value !== null && (
          <>
            <div className="bg-status-done h-full" style={{ width: `${value * 100}%` }} />
            {failed > 0 && <div className="bg-status-failed h-full flex-1" />}
          </>
        )}
      </div>
      <div className="flex justify-between gap-2 text-xs">
        <span className={toneClass[tone].text}>成功率 {percent(value)}</span>
        <span className="text-muted-foreground">失败 {failed}</span>
      </div>
      {note && <div className="text-muted-foreground text-xs">{note}</div>}
    </Panel>
  );
}

function CacheTile({ usage }: { usage: Usage }) {
  const rows = usage.data;
  return (
    <Panel className="space-y-2 p-4">
      <div className="text-muted-foreground text-xs">AI 缓存命中率</div>
      <div className="font-mono text-2xl font-semibold tabular-nums">
        {percent(cacheRate(rows))}
      </div>
      <div className="text-muted-foreground text-xs">
        {usage.error
          ? "分析服务暂时读不到"
          : `命中 ${compact(sum(rows, "cached_tokens"))} / 输入 ${compact(sum(rows, "cache_input_tokens"))} token`}
      </div>
      <div className="text-muted-foreground text-xs">AI 调用 {compact(sum(rows, "calls"))} 次</div>
    </Panel>
  );
}
