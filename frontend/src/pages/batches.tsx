import { AlertTriangle, Plus, Search } from "lucide-react";
import { useState } from "react";
import { Link, Navigate, NavLink, Outlet } from "react-router";
import { $api, type Schemas } from "@/api/client";
import { Notice, Panel, QueryState } from "@/components/page";
import { SegmentBar, StatusOf } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { shortTime } from "@/lib/format";
import { batchStatus, creatorFinal, creatorProgress, ordered, total } from "@/lib/status";
import { cn } from "@/lib/utils";

type BatchRow = Schemas["BatchRow"];

const FILTERS = [
  [undefined, "全部"],
  ["running", "进行中"],
  ["paused", "已暂停"],
  ["completed", "已完成"],
] as const;

/** 左侧列表取最近的批次，名称和状态在页面上筛选。 */
function useBatches(refetchOnMount?: "always") {
  return $api.useQuery(
    "get",
    "/api/batches",
    { params: { query: { size: 200 } } },
    { refetchOnMount },
  );
}

/** 批次工作台：左侧批次列表，右侧显示选中的批次。 */
export function BatchesPage() {
  const batches = useBatches();
  const [keyword, setKeyword] = useState("");
  const [status, setStatus] = useState<Schemas["BatchStatus"]>();

  return (
    <div className="grid items-start gap-5 lg:grid-cols-[296px_minmax(0,1fr)]">
      <Panel className="flex flex-col overflow-hidden lg:sticky lg:top-6 lg:max-h-[calc(100vh-3rem)]">
        <div className="space-y-2.5 border-b p-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">
              批次{" "}
              <span className="text-muted-foreground font-mono text-xs font-normal">
                {batches.data?.total}
              </span>
            </h2>
            <Button
              asChild
              size="sm"
              className="bg-signal text-signal-foreground hover:bg-signal/90"
            >
              <Link to="/batches/new">
                <Plus /> 新建批次
              </Link>
            </Button>
          </div>
          <div className="relative">
            <Search className="text-muted-foreground absolute top-2 left-2.5 size-4" />
            <Input
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              placeholder="搜索批次名称"
              aria-label="搜索批次名称"
              className="h-8 pl-8"
            />
          </div>
          <div className="flex gap-1">
            {FILTERS.map(([value, label]) => {
              const count = value
                ? batches.data?.items.filter((batch) => batch.status === value).length
                : batches.data?.items.length;
              return (
                <button
                  key={label}
                  type="button"
                  onClick={() => setStatus(value)}
                  aria-pressed={status === value}
                  className={cn(
                    "text-muted-foreground hover:text-foreground flex-1 rounded-md px-1.5 py-1 text-xs transition-colors",
                    status === value && "bg-secondary text-foreground font-medium",
                  )}
                >
                  {label} <span className="font-mono">{count ?? ""}</span>
                </button>
              );
            })}
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <QueryState query={batches} isEmpty={(data) => !data.total} empty="还没有批次">
            {(data) => {
              const shown = data.items.filter(
                (batch) =>
                  (!status || batch.status === status) &&
                  batch.name.toLowerCase().includes(keyword.trim().toLowerCase()),
              );
              return shown.length ? (
                <ul className="divide-y">
                  {shown.map((batch) => (
                    <BatchItem key={batch.id} batch={batch} />
                  ))}
                </ul>
              ) : (
                <Notice>没有符合条件的批次</Notice>
              );
            }}
          </QueryState>
        </div>
      </Panel>
      <div className="min-w-0 space-y-4">
        <Outlet />
      </div>
    </div>
  );
}

/** 列表中的一个批次：名称、状态、达人进度和需要处理的数量。 */
function BatchItem({ batch }: { batch: BatchRow }) {
  const { creators, videos } = batch;
  const finished = creatorFinal.reduce((sum, status) => sum + (creators[status] ?? 0), 0);
  const problems = {
    失败账号: creators.failed ?? 0,
    需要重新抓取: creators.recrawl ?? 0,
    失败视频: videos.failed ?? 0,
    待核查视频: videos.needs_review ?? 0,
  };
  const problemCount = total(problems);
  return (
    <li>
      <NavLink
        to={`/batches/${batch.id}`}
        className={({ isActive }) =>
          cn(
            "hover:bg-muted/60 block space-y-1.5 border-l-2 border-transparent px-3 py-2.5 transition-colors",
            isActive && "border-signal bg-accent/50 hover:bg-accent/50",
          )
        }
      >
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">{batch.name}</span>
          <span className="ml-auto shrink-0">
            <StatusOf labels={batchStatus} value={batch.status} />
          </span>
        </div>
        <SegmentBar
          segments={ordered(creatorProgress, batch.creator_progress)}
          className="gap-px"
        />
        <div className="text-muted-foreground flex items-center gap-3 text-[11px]">
          <span className="font-mono">{shortTime(batch.created_at)}</span>
          <span>
            达人 <span className="text-foreground font-mono">{finished}</span>/{total(creators)}
          </span>
          <span>
            视频 <span className="text-foreground font-mono">{videos.completed ?? 0}</span>/
            {total(videos)}
          </span>
          {problemCount > 0 && (
            <span
              className="text-status-failed ml-auto inline-flex items-center gap-1 font-mono"
              title={Object.entries(problems)
                .filter(([, count]) => count)
                .map(([label, count]) => `${label} ${count}`)
                .join(" · ")}
            >
              <AlertTriangle className="size-3" />
              {problemCount}
            </span>
          )}
        </div>
      </NavLink>
    </li>
  );
}

/** 没有选中批次时打开最近的一个。 */
export function FirstBatch() {
  // 从已删除的批次返回时，等列表更新后再选择，避免打开缓存里的旧批次。
  const { data, isError, isFetching } = useBatches("always");
  if (!data || isError || isFetching) return null;
  const [first] = data.items;
  return first ? (
    <Navigate to={`/batches/${first.id}`} replace />
  ) : (
    <Panel>
      <Notice>还没有批次，点“新建批次”上传名单或粘贴链接。</Notice>
    </Panel>
  );
}
