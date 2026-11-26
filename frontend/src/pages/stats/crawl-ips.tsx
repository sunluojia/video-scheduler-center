import { keepPreviousData } from "@tanstack/react-query";
import { useId, useState } from "react";
import { $api, type Schemas } from "@/api/client";
import { Pager } from "@/components/data-table";
import { Metric, Notice, Panel, Section } from "@/components/page";
import { StatusOf } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { dateTime, duration, shortTime } from "@/lib/format";
import { batchStatus, type Tone } from "@/lib/status";
import { cn } from "@/lib/utils";
import { CrawlIpCharts } from "./crawl-ip-charts";
import {
  accountNotes,
  type CrawlIp,
  formatIpMetric,
  HIGH_RISK_PCT,
  isHighRisk,
  MIN_RISK_ATTEMPTS,
  pct,
  RISK_LABELS,
} from "./crawl-ip-view";

const BATCHES_PER_PAGE = 50;
const REFRESH = { refetchInterval: 60_000, retry: false } as const;
const NO_DATA = {
  no_batch: "还没有可查看的采集批次。",
  no_leases: "该批次暂无可用的采集租约明细。",
  history_unavailable: "该历史批次暂无可用的采集租约明细，租约保留 24 小时。",
} as const;
const BACKOFF: Record<CrawlIp["backoff_state"], [string, Tone]> = {
  normal: ["正常", "done"],
  cooling: ["退避中", "attention"],
  probing: ["恢复试探", "attention"],
};
const COLUMNS = [
  "代理名称 / 出口 IP",
  "采集达人数",
  "采集占比",
  "采集时速",
  "采集尝试",
  "风控次数",
  "自身风控率",
  "全批次风控占比",
  "风控细分",
  "空正文退避",
];

type Batch = Schemas["CrawlIpBatch"];

export function CrawlIpPanel() {
  const selectId = useId();
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Batch>();
  const choices = $api.useQuery(
    "get",
    "/api/stats/crawl-ip-batches",
    {
      params: { query: { size: BATCHES_PER_PAGE, page } },
    },
    { ...REFRESH, placeholderData: keepPreviousData },
  );
  const stats = $api.useQuery(
    "get",
    "/api/stats/crawl-ips",
    {
      params: { query: selected ? { batch_id: selected.id } : {} },
    },
    REFRESH,
  );
  const batch = stats.data?.batch ?? selected;
  const pageItems = choices.data?.items ?? [];
  const options =
    batch && !pageItems.some((item) => item.id === batch.id) ? [batch, ...pageItems] : pageItems;

  return (
    <Section title="任务各 IP 采集与风控看板" className="min-w-0">
      <Panel>
        <div className="flex flex-wrap items-center justify-between gap-3 p-3 sm:p-4">
          <p className="text-muted-foreground text-xs">
            按批次统计仍保留的采集租约（保留 24 小时）
          </p>
          <div className="flex w-full min-w-0 flex-wrap items-center gap-2 sm:w-auto">
            <label htmlFor={selectId} className="text-muted-foreground text-xs">
              采集批次
            </label>
            <Select
              value={batch?.id ?? ""}
              disabled={!options.length}
              onValueChange={(id) => setSelected(options.find((item) => item.id === id))}
            >
              <SelectTrigger id={selectId} size="sm" className="w-full min-w-0 sm:w-72">
                <SelectValue placeholder={choices.isPending ? "正在加载批次" : "选择批次"} />
              </SelectTrigger>
              <SelectContent position="popper" align="end" className="max-w-[calc(100vw-1.5rem)]">
                <SelectItem value="auto">自动选择最近批次</SelectItem>
                {options.map((item) => (
                  <SelectItem
                    key={item.id}
                    value={item.id}
                    className="wrap-anywhere whitespace-normal"
                  >
                    {item.name} · {batchStatus[item.status][0]}
                    {item.has_leases ? "" : " · 无租约"}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {choices.isError && (
              <Button size="sm" variant="outline" onClick={() => void choices.refetch()}>
                重试加载批次
              </Button>
            )}
          </div>
        </div>
        {choices.data && choices.data.total > BATCHES_PER_PAGE && (
          <Pager page={page} size={BATCHES_PER_PAGE} total={choices.data.total} onPage={setPage} />
        )}
        <div className="border-t">
          {stats.isPending ? (
            <div role="status" className="space-y-2 p-4">
              <span className="sr-only">正在加载采集 IP 数据</span>
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-20 w-full" />
            </div>
          ) : !stats.data ? (
            <Notice>
              <span role="alert">采集 IP 数据加载失败</span>
              <Button size="sm" variant="outline" onClick={() => void stats.refetch()}>
                重试
              </Button>
            </Notice>
          ) : stats.data.data_status !== "available" ? (
            <Notice>{NO_DATA[stats.data.data_status]}</Notice>
          ) : (
            <CrawlIpBoard stats={stats.data} />
          )}
          {stats.isError && stats.data && (
            <p role="status" className="text-muted-foreground px-4 pb-3 text-xs">
              刷新失败，正在显示上次加载的采集 IP 数据。
            </p>
          )}
        </div>
      </Panel>
    </Section>
  );
}

function CrawlIpBoard({ stats }: { stats: Schemas["CrawlIpStats"] }) {
  const summary = stats.summary;
  const displayAccounts = accountNotes(summary);
  return (
    <div className="min-w-0 space-y-4 p-3 sm:p-4">
      <div className="grid grid-cols-2 gap-x-4 gap-y-3 wrap-anywhere sm:grid-cols-3 xl:grid-cols-5">
        <Metric label="活跃 IP 数" value={summary.active_ips.toLocaleString("zh-CN")} />
        <Metric label="总采集达人数" value={summary.collected_creators.toLocaleString("zh-CN")} />
        <Metric
          label="合计采集时速"
          value={formatIpMetric("creators_per_hour", summary.creators_per_hour)}
        />
        <Metric label="总风控次数" value={summary.risk_count.toLocaleString("zh-CN")} />
        <Metric label="整体风控率" value={pct(summary.risk_rate_pct)} />
      </div>
      <div className="text-muted-foreground space-y-1 text-xs leading-relaxed">
        {summary.first_lease_at && summary.last_lease_at && (
          <p>
            租约窗口 {shortTime(summary.first_lease_at)} 至 {shortTime(summary.last_lease_at)} ·{" "}
            {duration(summary.first_lease_at, summary.last_lease_at)}
            {displayAccounts && ` · ${displayAccounts}`}
          </p>
        )}
        <p>
          采集人数在批次内按账号去重，优先归给首个读完列表的 IP。各 IP
          用同一批次首末租约间隔折算时速，满 1 分钟后显示。
        </p>
        <p>
          风控率按全部采集尝试计算。至少 {MIN_RISK_ATTEMPTS} 次尝试且风控率达 {HIGH_RISK_PCT}%
          时标记为偏高。
        </p>
      </div>
      <CrawlIpCharts lines={stats.lines} />
      <CrawlIpTable lines={stats.lines} />
    </div>
  );
}

function CrawlIpTable({ lines }: { lines: CrawlIp[] }) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
        <h3 className="text-sm font-semibold">逐 IP 明细</h3>
        <span className="text-muted-foreground text-xs xl:hidden">横向滚动查看更多列</span>
      </div>
      <div
        role="region"
        aria-label="采集 IP 明细滚动区域"
        tabIndex={0}
        className="isolate max-h-[28rem] overflow-auto rounded-md border focus-visible:outline-2"
      >
        <table className="w-full text-xs">
          <caption className="sr-only">采集 IP 明细</caption>
          <thead className="bg-secondary text-muted-foreground sticky top-0 z-20">
            <tr>
              {COLUMNS.map((label, index) => (
                <th
                  key={label}
                  scope="col"
                  className={cn(
                    "px-3 py-2 font-medium whitespace-nowrap",
                    index > 0 && index < 8 ? "text-right" : "text-left",
                    index === 0 && "bg-secondary sticky left-0 border-r",
                  )}
                >
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => (
              <CrawlIpTableRow key={line.line} line={line} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CrawlIpTableRow({ line }: { line: CrawlIp }) {
  const high = isHighRisk(line);
  const metrics = [
    formatIpMetric("collected_creators", line.collected_creators),
    pct(line.creator_share_pct),
    formatIpMetric("creators_per_hour", line.creators_per_hour),
    line.crawl_leases.toLocaleString("zh-CN"),
    formatIpMetric("risk_count", line.risk_count),
    pct(line.risk_rate_pct),
    pct(line.risk_share_pct),
  ];
  const accounts = accountNotes(line);
  const risks = (Object.keys(RISK_LABELS) as (keyof typeof RISK_LABELS)[]).filter(
    (key) => line.risk_breakdown[key] > 0,
  );
  return (
    <tr className={cn("border-t", high && "bg-status-attention/10")}>
      <th
        scope="row"
        className="bg-card sticky left-0 z-10 max-w-48 min-w-36 border-r px-3 py-2 text-left font-medium wrap-anywhere"
      >
        {line.proxy_name !== line.line && <div>{line.proxy_name}</div>}
        <div className="text-muted-foreground font-mono font-normal">{line.line}</div>
      </th>
      {metrics.map((value, index) => (
        <td
          key={COLUMNS[index + 1]}
          className="px-3 py-2 text-right align-top font-mono whitespace-nowrap"
        >
          {value}
          {index === 0 && accounts && (
            <div className="text-muted-foreground font-sans whitespace-normal">{accounts}</div>
          )}
          {index === 5 && high && (
            <Badge variant="outline" className="text-status-failed mt-1 ml-auto flex">
              偏高
            </Badge>
          )}
        </td>
      ))}
      <td className="px-3 py-2 align-top whitespace-nowrap">
        {risks.length
          ? risks.map((key) => (
              <div key={key}>
                {RISK_LABELS[key]} <span className="font-mono">{line.risk_breakdown[key]}</span>
              </div>
            ))
          : "—"}
      </td>
      <td className="px-3 py-2 align-top whitespace-nowrap">
        <StatusOf labels={BACKOFF} value={line.backoff_state} />
        {line.backoff_step > 0 && (
          <div className="text-muted-foreground">第 {line.backoff_step} 阶</div>
        )}
        {line.backoff_until && (
          <time
            dateTime={line.backoff_until}
            title={dateTime(line.backoff_until)}
            className="text-muted-foreground"
          >
            {shortTime(line.backoff_until)} 结束
          </time>
        )}
      </td>
    </tr>
  );
}
