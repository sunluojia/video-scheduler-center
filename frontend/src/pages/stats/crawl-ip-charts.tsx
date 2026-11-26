import { useState } from "react";
import { Choice } from "@/components/choice";
import { cn } from "@/lib/utils";
import {
  chartBars,
  COLLECTION_METRICS,
  type CrawlIp,
  type MetricKey,
  RISK_METRICS,
} from "./crawl-ip-view";

export function CrawlIpCharts({ lines }: { lines: CrawlIp[] }) {
  const [collection, setCollection] =
    useState<keyof typeof COLLECTION_METRICS>("collected_creators");
  const [risk, setRisk] = useState<keyof typeof RISK_METRICS>("risk_count");

  return (
    <div className="grid items-start gap-4 lg:grid-cols-2">
      <div className="min-w-0 space-y-3 rounded-md border p-3 sm:p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 [&>[role=group]]:flex-wrap">
          <h3 className="text-sm font-semibold">采集表现</h3>
          <Choice
            label="采集指标"
            options={COLLECTION_METRICS}
            value={collection}
            onChange={setCollection}
          />
        </div>
        <CrawlIpBars lines={lines} metric={collection} label={COLLECTION_METRICS[collection]} />
      </div>
      <div className="min-w-0 space-y-3 rounded-md border p-3 sm:p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 [&>[role=group]]:flex-wrap">
          <h3 className="text-sm font-semibold">风控对比</h3>
          <Choice label="风控指标" options={RISK_METRICS} value={risk} onChange={setRisk} />
        </div>
        <CrawlIpBars lines={lines} metric={risk} label={RISK_METRICS[risk]} risk />
      </div>
    </div>
  );
}

function CrawlIpBars({
  lines,
  metric,
  label,
  risk,
}: {
  lines: CrawlIp[];
  metric: MetricKey;
  label: string;
  risk?: boolean;
}) {
  const bars = chartBars(lines, metric);
  if (!bars.length) {
    return <p className="text-muted-foreground py-4 text-sm">租约跨度满 1 分钟后显示时速。</p>;
  }
  return (
    <ul
      aria-label={`${label}对比图`}
      tabIndex={bars.length > 8 ? 0 : undefined}
      className="max-h-72 space-y-2.5 overflow-y-auto pr-1 focus-visible:outline-2"
    >
      {bars.map(({ line, label, width }) => (
        <li key={line.line} className="space-y-1">
          <div className="flex min-w-0 items-baseline justify-between gap-2 text-xs">
            <span className="min-w-0 truncate" title={line.proxy_name}>
              {line.proxy_name}
              {line.proxy_name !== line.line && (
                <span className="text-muted-foreground ml-2 font-mono">{line.line}</span>
              )}
            </span>
            <span className="shrink-0 font-mono tabular-nums">{label}</span>
          </div>
          <div aria-hidden="true" className="bg-muted h-1.5 overflow-hidden rounded-full">
            <div
              className={cn("h-full rounded-full", risk ? "bg-status-attention" : "bg-series-1")}
              style={{ width: `${width}%` }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}
