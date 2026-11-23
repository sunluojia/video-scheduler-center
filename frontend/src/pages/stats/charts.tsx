import { useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Schemas } from "@/api/client";
import { Choice } from "@/components/choice";
import { compact, percent } from "@/lib/format";
import { cacheRate, sum, type UsageRow } from "@/lib/usage";
import { cn } from "@/lib/utils";
import { FUNNEL, rate } from "./insights";
import { type Bucket, bucketLabel } from "./period";

type Row = { at: string } & Record<string, number | string | null>;
type Series = { key: string; label: string; color: string; total: string };
type Kind = "line" | "bar" | "area" | "table";

const KINDS: Record<Kind, string> = { line: "折线", bar: "柱状", area: "面积", table: "表格" };
const TREND = { ok: "成功数", failed: "失败数", rate: "成功率" } as const;
const AI = { cache: "缓存命中率", calls: "调用次数" } as const;
const CHARTS = { line: LineChart, bar: BarChart, area: AreaChart };

/** 五个环节随时间的变化：可以看成功数、失败数或成功率，点图例隐藏某个环节。 */
export function Trend({ stats }: { stats: Schemas["Stats"] }) {
  const [metric, setMetric] = useState<keyof typeof TREND>("ok");
  const [kind, setKind] = useState<Kind>("line");
  const [hidden, setHidden] = useState<string[]>([]);
  const { totals } = stats;
  const series = FUNNEL.map((step, index) => ({
    key: step.ok,
    label: step.label,
    color: `var(--series-${index + 1})`,
    total:
      metric === "rate"
        ? percent(rate(totals[step.ok], totals[step.failed]))
        : compact(totals[metric === "ok" ? step.ok : step.failed]),
  }));
  const data = stats.timeline.map((row) => ({
    at: row.at,
    ...Object.fromEntries(
      FUNNEL.map((step) => [
        step.ok,
        metric === "rate"
          ? rate(row[step.ok], row[step.failed])
          : row[metric === "ok" ? step.ok : step.failed],
      ]),
    ),
  }));
  const shown = series.filter((item) => !hidden.includes(item.key));
  const toggle = (key: string) =>
    setHidden((keys) =>
      keys.includes(key) ? keys.filter((item) => item !== key) : [...keys, key],
    );
  return (
    <div className="space-y-3 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Choice label="指标" options={TREND} value={metric} onChange={setMetric} />
        <Choice label="图表类型" options={KINDS} value={kind} onChange={setKind} />
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {series.map((item) => (
          <button
            key={item.key}
            type="button"
            aria-pressed={!hidden.includes(item.key)}
            onClick={() => toggle(item.key)}
            className={cn(
              "text-muted-foreground inline-flex items-center gap-1.5 text-xs",
              hidden.includes(item.key) && "opacity-40",
            )}
          >
            <span className="size-2 rounded-[2px]" style={{ background: item.color }} />
            {item.label}
            <span className="text-foreground font-mono">{item.total}</span>
          </button>
        ))}
      </div>
      <TimeView
        data={data}
        series={shown}
        kind={kind}
        bucket={stats.bucket}
        format={metric === "rate" ? (value) => percent(value, 0) : compact}
        ratio={metric === "rate"}
      />
    </div>
  );
}

/** AI 调用随时间的变化：缓存命中率或调用次数。 */
export function AiTrend({
  timeline,
  usage,
  bucket,
}: {
  timeline: Schemas["TimelineRow"][];
  usage: UsageRow[];
  bucket: Bucket;
}) {
  const [metric, setMetric] = useState<keyof typeof AI>("cache");
  const [kind, setKind] = useState<Kind>("line");
  const data = timeline.map(({ at }) => {
    const rows = usage.filter((row) => new Date(row.at).getTime() === new Date(at).getTime());
    return { at, value: metric === "cache" ? cacheRate(rows) : sum(rows, "calls") };
  });
  const total = metric === "cache" ? percent(cacheRate(usage)) : compact(sum(usage, "calls"));
  return (
    <div className="space-y-3 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Choice label="AI 指标" options={AI} value={metric} onChange={setMetric} />
        <Choice label="图表类型" options={KINDS} value={kind} onChange={setKind} />
      </div>
      <div className="text-muted-foreground text-xs">
        这段时间合计 <span className="text-foreground font-mono">{total}</span>
      </div>
      <TimeView
        data={data}
        series={[{ key: "value", label: AI[metric], color: "var(--series-1)", total }]}
        kind={kind}
        bucket={bucket}
        format={metric === "cache" ? (value) => percent(value, 0) : compact}
        ratio={metric === "cache"}
        height={220}
      />
    </div>
  );
}

/** 时间序列：折线、柱状、面积或表格；悬停显示这个时段每个系列的数值。 */
function TimeView({
  data,
  series,
  kind,
  bucket,
  format,
  ratio,
  height = 280,
}: {
  data: Row[];
  series: Series[];
  kind: Kind;
  bucket: Bucket;
  format: (value: number) => string;
  ratio: boolean;
  height?: number;
}) {
  if (kind === "table")
    return <TimeTable data={data} series={series} bucket={bucket} format={format} />;
  const Chart = CHARTS[kind];
  const tick = { fill: "var(--muted-foreground)", fontSize: 11 };
  return (
    <ResponsiveContainer width="100%" height={height}>
      <Chart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barGap={2}>
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis
          dataKey="at"
          tickFormatter={(at: string) => bucketLabel(at, bucket)}
          tick={tick}
          tickLine={false}
          axisLine={{ stroke: "var(--border)" }}
          minTickGap={24}
        />
        <YAxis
          tickFormatter={format}
          tick={tick}
          tickLine={false}
          axisLine={false}
          width={48}
          allowDecimals={ratio}
          domain={ratio ? [0, 1] : [0, "auto"]}
        />
        <Tooltip
          cursor={
            kind === "bar"
              ? { fill: "var(--muted)" }
              : { stroke: "var(--muted-foreground)", strokeDasharray: "3 3" }
          }
          content={({ active, label }) =>
            active && (
              <Tip
                title={bucketLabel(String(label), bucket, true)}
                series={series}
                row={data.find((row) => row.at === label)}
                format={format}
              />
            )
          }
        />
        {series.map((item) => {
          const common = {
            dataKey: item.key,
            name: item.label,
            isAnimationActive: false,
          };
          // 折线和面积：前后时段都没有数据的点连不成线，单独画一个点
          const lone = ({ index = 0, cx, cy }: { index?: number; cx?: number; cy?: number }) =>
            cy != null &&
            data[index - 1]?.[item.key] == null &&
            data[index + 1]?.[item.key] == null ? (
              <circle key={index} cx={cx} cy={cy} r={3} fill={item.color} />
            ) : (
              <g key={index} />
            );
          const line = {
            type: "monotone",
            stroke: item.color,
            strokeWidth: 2,
            dot: lone,
            activeDot: { r: 4, stroke: "var(--card)", strokeWidth: 2 },
          } as const;
          if (kind === "bar") {
            return (
              <Bar
                key={item.key}
                {...common}
                fill={item.color}
                radius={[2, 2, 0, 0]}
                maxBarSize={16}
              />
            );
          }
          if (kind === "area") {
            return (
              <Area key={item.key} {...common} {...line} fill={item.color} fillOpacity={0.08} />
            );
          }
          return <Line key={item.key} {...common} {...line} />;
        })}
      </Chart>
    </ResponsiveContainer>
  );
}

/** 悬停提示：文字用正文颜色，色块标出是哪个系列。 */
function Tip({
  title,
  series,
  row,
  format,
}: {
  title: string;
  series: Series[];
  row: Row | undefined;
  format: (value: number) => string;
}) {
  return (
    <div className="bg-popover min-w-40 space-y-1 rounded-md border px-3 py-2 text-xs shadow-md">
      <div className="text-muted-foreground font-mono">{title}</div>
      {series.map((item) => (
        <div key={item.key} className="flex items-center gap-2">
          <span className="size-2 rounded-[2px]" style={{ background: item.color }} />
          <span className="text-muted-foreground">{item.label}</span>
          <span className="text-foreground ml-auto pl-4 font-mono">
            {show(row?.[item.key], format)}
          </span>
        </div>
      ))}
    </div>
  );
}

function TimeTable({
  data,
  series,
  bucket,
  format,
}: {
  data: Row[];
  series: Series[];
  bucket: Bucket;
  format: (value: number) => string;
}) {
  return (
    <div className="max-h-80 overflow-auto rounded-md border">
      <table className="w-full text-sm">
        <thead className="bg-muted/60 text-muted-foreground sticky top-0 text-xs">
          <tr>
            <th className="px-3 py-2 text-left font-normal">时段</th>
            {series.map((item) => (
              <th key={item.key} className="px-3 py-2 text-right font-normal">
                {item.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="font-mono">
          {data.map((row) => (
            <tr key={row.at} className="border-t">
              <td className="px-3 py-1.5">{bucketLabel(row.at, bucket, true)}</td>
              {series.map((item) => (
                <td key={item.key} className="px-3 py-1.5 text-right">
                  {show(row[item.key], format)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function show(value: number | string | null | undefined, format: (value: number) => string) {
  return typeof value === "number" ? format(value) : "—";
}
