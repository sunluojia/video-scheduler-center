import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import {
  ChartColumn,
  ChevronRight,
  Download,
  ExternalLink,
  ListCollapse,
  ListTree,
  LoaderCircle,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  Trash2,
  X,
  XCircle,
} from "lucide-react";
import { Fragment, type ReactNode, useEffect, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router";
import { toast } from "sonner";
import { $api, errorText, fetchClient, type Schemas } from "@/api/client";
import { AiBatchBadge, AiBatchSwitch } from "@/components/ai-batch-mode";
import { Confirm } from "@/components/confirm";
import { CreatorSummaryTags } from "@/components/creator-summary-tags";
import { DataTable, Pager } from "@/components/data-table";
import { Elapsed, Panel, QueryState, Tags } from "@/components/page";
import { Legend, SegmentBar, StatusOf } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ago, dateTime } from "@/lib/format";
import { useFilters } from "@/lib/params";
import { useDone } from "@/lib/refresh";
import {
  batchStatus,
  creatorFinal,
  creatorFlow,
  creatorProgress,
  creatorStatus,
  ordered,
  toneClass,
  total,
  workStatus,
} from "@/lib/status";
import { cn } from "@/lib/utils";

const SIZE = 50;
const SLOTS = [1, 2, 3, 4, 5];
const PROBLEMS: Schemas["WorkStatus"][] = ["failed", "needs_review"];

type BatchRow = Schemas["BatchRow"];
type CreatorRow = Schemas["CreatorRow"];
type CreatorProgress = Schemas["CreatorProgress"];
type CreatorFilter = CreatorProgress | "analyzing";
type Filters = ReturnType<typeof useFilters<"status" | "q" | "tab">>;

function creatorFilter(value: string | undefined): CreatorFilter | undefined {
  if (value === "analyzing") return value;
  return value && Object.hasOwn(creatorProgress, value) ? (value as CreatorProgress) : undefined;
}

/** 工作台右侧：选中批次的状态、操作、达人和问题视频。 */
export function BatchPanel() {
  const batchId = useParams().batchId!;
  const path = "/api/batches/{batch_id}";
  const init = { params: { path: { batch_id: batchId } } };
  const { data, error, isPending } = useQuery({
    queryKey: $api.queryOptions("get", path, init).queryKey,
    queryFn: async ({ signal }) => {
      const result = await fetchClient.GET(path, { ...init, signal });
      if (result.response.status === 404) return null;
      if (result.data === undefined) throw result.error ?? new Error("无法读取批次详情");
      return result.data;
    },
  });
  const filters = useFilters(["status", "q", "tab"] as const);
  const { values, set } = filters;
  const status = creatorFilter(values.status);

  if (data === null) return <Navigate to="/batches" replace />;

  return (
    <QueryState query={{ data, error, isPending }} rows={8}>
      {(data) => (
        <>
          <Header batch={data} />
          <StatusStrip
            batch={data}
            status={status}
            onStatus={(status) => set({ status, tab: undefined })}
          />
          <Tabs
            value={values.tab ?? "creators"}
            onValueChange={(tab) => set({ tab: tab === "creators" ? undefined : tab })}
            className="rise gap-3"
          >
            <TabsList>
              <TabsTrigger value="creators">
                达人 <Count value={total(data.creators)} />
              </TabsTrigger>
              <TabsTrigger value="problems">
                问题视频{" "}
                <Count value={(data.videos.failed ?? 0) + (data.videos.needs_review ?? 0)} />
              </TabsTrigger>
            </TabsList>
            <TabsContent value="creators">
              <Creators batchId={batchId} status={status} filters={filters} />
            </TabsContent>
            <TabsContent value="problems">
              <Problems batchId={batchId} filters={filters} />
            </TabsContent>
          </Tabs>
        </>
      )}
    </QueryState>
  );
}

function Hint({ text, children }: { text: string; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent>{text}</TooltipContent>
    </Tooltip>
  );
}

function Count({ value }: { value: number }) {
  return <span className="text-muted-foreground font-mono text-xs">{value}</span>;
}

function Header({ batch }: { batch: BatchRow }) {
  const navigate = useNavigate();
  const done = useDone();
  const queryClient = useQueryClient();
  const path = { params: { path: { batch_id: batch.id } } };
  const control = {
    onSuccess: (result: Schemas["BatchAction"]) => done("已更新批次状态", result.warnings),
  };
  const start = $api.useMutation("post", "/api/batches/{batch_id}/start", {
    onSuccess: () => done("批次已开始"),
  });
  const batchMode = $api.useMutation("put", "/api/batches/{batch_id}/ai-batch-mode", {
    onSuccess: ({ batch: updated, warnings }) => {
      const key = $api.queryOptions("get", "/api/batches/{batch_id}", path).queryKey;
      queryClient.setQueryData<BatchRow>(
        key,
        (current) => current && { ...current, ai_batch_mode: updated.ai_batch_mode },
      );
      done(updated.ai_batch_mode ? "AI 批量模式已启用" : "AI 批量模式已关闭", warnings);
    },
  });
  const pause = $api.useMutation("post", "/api/batches/{batch_id}/pause", control);
  const resume = $api.useMutation("post", "/api/batches/{batch_id}/resume", control);
  const cancel = $api.useMutation("post", "/api/batches/{batch_id}/cancel", control);
  const retry = $api.useMutation("post", "/api/batches/{batch_id}/retry", {
    onSuccess: (result) => done(`已补跑 ${result.count} 个分析任务`),
  });
  const recrawl = $api.useMutation("post", "/api/batches/{batch_id}/recrawl", {
    onSuccess: (result) => done(`已重新爬取 ${result.count} 位达人`),
  });
  const remove = $api.useMutation("delete", "/api/batches/{batch_id}", {
    onSuccess: () => {
      done("已删除批次");
      void navigate("/batches", { replace: true });
    },
  });

  const active = batch.status === "running" || batch.status === "paused";
  const started = batch.status !== "draft" && batch.status !== "cancelled";
  return (
    <header className="rise flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 space-y-1">
        <h1 className="truncate text-xl font-semibold tracking-tight">{batch.name}</h1>
        <div className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <StatusOf labels={batchStatus} value={batch.status} />
          {batch.ai_batch_mode && <AiBatchBadge />}
          <span>{batch.source}</span>
          <span>创建于 {dateTime(batch.created_at)}</span>
          {batch.started_at && (
            <span>
              {batch.finished_at ? "共用" : "已用"}{" "}
              <Elapsed start={batch.started_at} end={batch.finished_at} />
            </span>
          )}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {batch.status === "draft" && (
          <Button
            size="sm"
            className="bg-signal text-signal-foreground hover:bg-signal/90"
            onClick={() => start.mutate(path)}
          >
            <Play /> 开始
          </Button>
        )}
        {batch.status === "running" && (
          <Hint text="不再抓取新的达人；已在下载、识别、汇总的视频继续处理完">
            <Button size="sm" variant="outline" onClick={() => pause.mutate(path)}>
              <Pause /> 暂停采集
            </Button>
          </Hint>
        )}
        {batch.status === "paused" && (
          <Hint text="接着抓取剩下的达人">
            <Button size="sm" variant="outline" onClick={() => resume.mutate(path)}>
              <Play /> 恢复采集
            </Button>
          </Hint>
        )}
        {started && (
          <>
            <Button
              size="sm"
              variant="outline"
              onClick={() => retry.mutate(path)}
              disabled={retry.isPending}
            >
              <RotateCcw /> 补跑失败
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => recrawl.mutate(path)}
              disabled={!batch.creators.recrawl}
            >
              <RefreshCw /> 重新抓取
            </Button>
          </>
        )}
        <BatchExportButton key={`${batch.id}-workbook`} batch={batch} report="workbook" />
        {batch.started_at && (
          <BatchExportButton key={`${batch.id}-attempts`} batch={batch} report="attempts" />
        )}
        {started && (
          <Button size="sm" variant="outline" asChild>
            <Link to={`/stats?batch=${batch.id}`}>
              <ChartColumn /> 数据看板
            </Link>
          </Button>
        )}
        {active ? (
          <Confirm
            title="取消批次"
            description="取消后不再发布新任务，执行程序会停止这个批次的任务。已得出的结果保留，可以导出。"
            action="取消批次"
            onConfirm={() => cancel.mutate(path)}
          >
            <Button size="sm" variant="ghost" className="text-status-failed">
              <XCircle /> 取消
            </Button>
          </Confirm>
        ) : (
          <Confirm
            title="删除批次"
            description="批次的名单、作品、分析结果和事件记录都会删除，不能恢复。"
            action="删除"
            onConfirm={() => remove.mutate(path)}
          >
            <Button size="sm" variant="ghost" className="text-status-failed">
              <Trash2 /> 删除
            </Button>
          </Confirm>
        )}
      </div>
      {(active || batch.status === "draft") && (
        <div className="w-full">
          <AiBatchSwitch
            label="AI 批量模式"
            checked={batch.ai_batch_mode}
            disabled={batchMode.isPending}
            onCheckedChange={(enabled) => batchMode.mutate({ ...path, body: { enabled } })}
          />
        </div>
      )}
    </header>
  );
}

const EXPORT_TIMEOUT_MS = 180_000;
const BATCH_REPORTS = {
  workbook: {
    path: "/api/batches/{batch_id}/export",
    mediaType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    filename: "达人标签结果.xlsx",
    name: "Excel",
    label: "导出 Excel",
  },
  attempts: {
    path: "/api/batches/{batch_id}/execution-attempts",
    mediaType: "text/csv",
    filename: "执行排障明细.csv",
    name: "执行排障明细",
    label: "执行排障明细",
  },
} as const;

function BatchExportButton({
  batch,
  report,
}: {
  batch: Pick<BatchRow, "id" | "name">;
  report: keyof typeof BATCH_REPORTS;
}) {
  const file = BATCH_REPORTS[report];
  const [exporting, setExporting] = useState(false);
  const activeRequest = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      activeRequest.current?.abort();
      activeRequest.current = null;
    },
    [],
  );

  async function downloadReport() {
    if (activeRequest.current) return;
    const request = new AbortController();
    activeRequest.current = request;
    const timeout = window.setTimeout(() => {
      request.abort(new DOMException(`生成 ${file.name} 超时，请重试`, "TimeoutError"));
    }, EXPORT_TIMEOUT_MS);
    setExporting(true);

    try {
      const { data, error, response } = await fetchClient.GET(file.path, {
        params: { path: { batch_id: batch.id } },
        parseAs: "blob",
        signal: request.signal,
      });
      if (!response.ok) {
        if (error && typeof error === "object") throw error;
        throw new Error(`${file.name} 导出失败（HTTP ${response.status}）`);
      }
      if (!data?.size || data.type.split(";", 1)[0] !== file.mediaType) {
        throw new Error(`未收到有效的 ${file.name} 文件，请重试`);
      }
      request.signal.throwIfAborted();
      const url = URL.createObjectURL(data);
      const download = document.createElement("a");
      download.href = url;
      download.download = `${batch.name}-${file.filename}`;
      document.body.append(download);
      download.click();
      download.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
      toast.success(`${file.name} 已生成，已发起下载`);
    } catch (error) {
      if (activeRequest.current === request) {
        toast.error(errorText(request.signal.aborted ? request.signal.reason : error));
      }
    } finally {
      window.clearTimeout(timeout);
      if (activeRequest.current === request) {
        activeRequest.current = null;
        setExporting(false);
      }
    }
  }

  return (
    <>
      <Button
        size="sm"
        variant="outline"
        disabled={exporting}
        onClick={() => void downloadReport()}
      >
        {exporting ? <LoaderCircle className="motion-safe:animate-spin" /> : <Download />}
        {exporting ? `正在生成 ${file.name}…` : file.label}
      </Button>
      <span role="status" aria-label={`${file.name} 导出进度`} className="sr-only">
        {exporting ? `正在生成 ${file.name}，大批次可能需要等待一段时间。` : ""}
      </span>
    </>
  );
}

/**
 * 达人状态带：按流程排列处理中的环节和最终结论，点一格只看这个状态的达人；
 * 下方是整体进度和视频状态。
 */
function StatusStrip({
  batch,
  status,
  onStatus,
}: {
  batch: BatchRow;
  status?: CreatorFilter;
  onStatus: (status?: CreatorProgress) => void;
}) {
  const { creator_progress: progress, videos } = batch;
  const all = total(progress);
  const finished = creatorFinal.reduce((sum, key) => sum + (progress[key] ?? 0), 0);
  const cell = (key: CreatorProgress) => {
    const [label, tone] = creatorProgress[key];
    const count = progress[key] ?? 0;
    return (
      <button
        key={key}
        type="button"
        onClick={() => onStatus(status === key ? undefined : key)}
        aria-pressed={status === key}
        className={cn(
          "bg-card hover:bg-muted relative flex min-w-0 flex-col gap-0.5 px-3 py-2.5 text-left transition-colors",
          status === key && "bg-accent hover:bg-accent",
        )}
      >
        <span className="text-muted-foreground flex items-center gap-1.5 text-xs whitespace-nowrap">
          <span className={cn("size-1.5 rounded-full", toneClass[tone].dot)} />
          {label}
        </span>
        <span
          className={cn(
            "font-mono text-xl font-semibold tabular-nums",
            !count && "text-muted-foreground/40",
          )}
        >
          {count}
        </span>
        {status === key && (
          <span className="bg-signal absolute inset-x-3 bottom-0 h-0.5 rounded-full" />
        )}
      </button>
    );
  };

  return (
    <Panel className="rise overflow-hidden">
      <div
        role="group"
        aria-label="处理阶段"
        className="bg-border grid grid-cols-2 gap-px md:grid-cols-3 2xl:grid-cols-6"
      >
        {creatorFlow.map(cell)}
      </div>
      <div
        role="group"
        aria-label="处理结果"
        className="bg-border grid grid-cols-2 gap-px border-t md:grid-cols-4"
      >
        {creatorFinal.map(cell)}
      </div>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-t px-3 py-2 text-xs">
        <Button size="xs" variant="outline" aria-pressed={!status} onClick={() => onStatus()}>
          全部达人 <Count value={all} />
        </Button>
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">达人已结束</span>
          <span className="font-mono">
            {finished}/{all}
          </span>
          <SegmentBar segments={ordered(creatorProgress, progress)} className="w-40 gap-px" />
          <span className="text-muted-foreground font-mono">
            {all ? Math.round((finished / all) * 100) : 0}%
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-muted-foreground">视频 {total(videos)}</span>
          <Legend segments={ordered(workStatus, videos)} />
        </div>
      </div>
    </Panel>
  );
}

/** 达人表：每行带五个作品格，点开直接看每条作品的状态、标签和失败原因。 */
function Creators({
  batchId,
  status,
  filters,
}: {
  batchId: string;
  status?: CreatorFilter;
  filters: Filters;
}) {
  const { values, page, set } = filters;
  const [expandAll, setExpandAll] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const query = $api.useQuery("get", "/api/batches/{batch_id}/creators", {
    params: {
      path: { batch_id: batchId },
      query: {
        status: status === "analyzing" ? status : undefined,
        progress: status === "analyzing" ? undefined : status,
        q: values.q,
        page,
        size: SIZE,
      },
    },
  });
  const isOpen = (id: string) => expanded[id] ?? expandAll;
  const toggle = (id: string) => setExpanded((current) => ({ ...current, [id]: !isOpen(id) }));

  return (
    <Panel>
      <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
        <div className="relative">
          <Search className="text-muted-foreground absolute top-2 left-2.5 size-4" />
          <Input
            key={values.q}
            defaultValue={values.q}
            placeholder="名称、账号或 oecuid，回车搜索"
            aria-label="搜索达人"
            className="h-8 w-64 pl-8"
            onKeyDown={(event) =>
              event.key === "Enter" && set({ q: event.currentTarget.value.trim() })
            }
          />
        </div>
        {status && (
          <Button variant="secondary" size="xs" onClick={() => set({ status: undefined })}>
            {status === "analyzing" ? creatorStatus.analyzing[0] : creatorProgress[status][0]} <X />
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto"
          onClick={() => {
            setExpandAll(!expandAll);
            setExpanded({});
          }}
        >
          {expandAll ? <ListCollapse /> : <ListTree />}
          {expandAll ? "全部收起" : "全部展开"}
        </Button>
      </div>
      <QueryState query={query} isEmpty={(data) => data.total === 0} empty="没有符合条件的达人">
        {(data) => (
          <>
            <Table className="text-[13px]">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="w-8" />
                  <TableHead className="text-muted-foreground h-8 w-12 text-xs">#</TableHead>
                  <TableHead className="text-muted-foreground h-8 text-xs">达人</TableHead>
                  <TableHead className="text-muted-foreground h-8 w-28 text-xs">状态</TableHead>
                  <TableHead className="text-muted-foreground h-8 w-40 text-xs">作品</TableHead>
                  <TableHead className="text-muted-foreground h-8 text-xs">汇总标签</TableHead>
                  <TableHead className="text-muted-foreground h-8 text-xs">说明</TableHead>
                  <TableHead className="text-muted-foreground h-8 w-24 text-xs">最近活动</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((creator) => (
                  <Fragment key={creator.id}>
                    <TableRow
                      className="cursor-pointer"
                      data-state={isOpen(creator.id) ? "selected" : undefined}
                      onClick={() => toggle(creator.id)}
                    >
                      <TableCell className="py-1.5 pr-0">
                        <button
                          type="button"
                          aria-expanded={isOpen(creator.id)}
                          aria-label={`展开 ${creator.name} 的作品`}
                          className="text-muted-foreground hover:text-foreground grid size-6 place-items-center rounded"
                          onClick={(event) => {
                            event.stopPropagation();
                            toggle(creator.id);
                          }}
                        >
                          <ChevronRight
                            className={cn(
                              "size-4 transition-transform",
                              isOpen(creator.id) && "rotate-90",
                            )}
                          />
                        </button>
                      </TableCell>
                      <TableCell className="text-muted-foreground py-1.5 font-mono text-xs">
                        {creator.position}
                      </TableCell>
                      <TableCell className="max-w-72 py-1.5">
                        <Link
                          to={`/creators/${creator.id}`}
                          onClick={(event) => event.stopPropagation()}
                          className="block truncate font-medium hover:underline"
                        >
                          {creator.name}
                        </Link>
                        <div className="text-muted-foreground truncate font-mono text-[11px]">
                          {creator.handle ? `@${creator.handle}` : "—"}
                          {creator.oecuid && ` · ${creator.oecuid}`}
                        </div>
                      </TableCell>
                      <TableCell className="py-1.5">
                        <StatusOf labels={creatorProgress} value={creator.progress} />
                      </TableCell>
                      <TableCell className="py-1.5">
                        <WorkSlots works={creator.works} />
                      </TableCell>
                      <TableCell className="py-1.5">
                        <CreatorSummaryTags creator={creator} max={6} />
                      </TableCell>
                      <TableCell className="text-muted-foreground max-w-64 py-1.5 text-xs">
                        <span className="line-clamp-2">{creator.note}</span>
                      </TableCell>
                      <TableCell className="text-muted-foreground py-1.5 text-xs whitespace-nowrap">
                        {ago(creator.updated_at)}
                      </TableCell>
                    </TableRow>
                    {isOpen(creator.id) && (
                      <TableRow className="bg-muted/30 hover:bg-muted/30">
                        <TableCell colSpan={8} className="p-0">
                          <WorkList creator={creator} />
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                ))}
              </TableBody>
            </Table>
            <Pager
              page={page}
              size={SIZE}
              total={data.total}
              onPage={(next) => set({ page: next })}
            />
          </>
        )}
      </QueryState>
    </Panel>
  );
}

/** 五个作品格：按位置显示每条作品的状态，虚线格表示这个位置没有作品。 */
function WorkSlots({ works }: { works: Schemas["WorkView"][] }) {
  const videos = works.filter((work) => work.kind === "video");
  return (
    <div className="flex items-center gap-1">
      {SLOTS.map((position) => {
        const work = works.find((item) => item.position === position);
        if (!work) {
          return (
            <span
              key={position}
              title={`#${position} 没有作品`}
              className="border-muted-foreground/30 size-3.5 rounded-[3px] border border-dashed"
            />
          );
        }
        const [label, tone] = workStatus[work.status];
        return (
          <span
            key={position}
            title={`#${position} ${label}`}
            className={cn(
              "size-3.5 rounded-[3px]",
              toneClass[tone].bar,
              tone === "active" && "animate-pulse",
            )}
          />
        );
      })}
      {videos.length > 0 && (
        <span className="text-muted-foreground ml-1.5 font-mono text-[11px]">
          {videos.filter((work) => work.status === "completed").length}/{videos.length}
        </span>
      )}
    </div>
  );
}

/** 展开的达人：逐条列出锁定的作品。 */
function WorkList({ creator }: { creator: CreatorRow }) {
  const { works } = creator;
  if (!works.length) {
    return (
      <p className="text-muted-foreground py-3 pl-[88px] text-xs">
        {creator.note ??
          (creator.status === "pending" || creator.status === "crawling"
            ? "正在等待爬取结果"
            : "没有锁定作品")}
      </p>
    );
  }
  return (
    <ul className="divide-border/60 divide-y py-1 pr-3 pl-[88px]">
      {works.map((work) => (
        <li
          key={work.id}
          // 每条作品各自成网格，列宽都要固定或按比例，各行才能对齐
          className="grid grid-cols-[28px_76px_minmax(0,1.1fr)_minmax(0,1fr)_64px] items-start gap-3 py-2 text-xs"
        >
          <span className="text-muted-foreground font-mono">#{work.position}</span>
          <StatusOf labels={workStatus} value={work.status} />
          {work.error ? (
            <p className="text-status-failed line-clamp-2">{work.error}</p>
          ) : (
            <Tags tags={work.tags} max={8} />
          )}
          <p className="text-muted-foreground line-clamp-2">
            {work.description || "没有发布描述"}
            {work.topics.map((topic) => ` #${topic}`)}
          </p>
          <div className="flex items-center justify-end gap-1">
            {work.kind === "video" && (
              <Button asChild variant="ghost" size="xs">
                <Link to={`/works/${work.id}`}>详情</Link>
              </Button>
            )}
            <Button asChild variant="ghost" size="icon-xs">
              <a href={work.url} target="_blank" rel="noreferrer" aria-label="打开原作品">
                <ExternalLink />
              </a>
            </Button>
          </div>
        </li>
      ))}
      {works.length < SLOTS.length && (
        <li className="text-muted-foreground py-2 text-xs">
          作品不足五条（实际 {works.length} 条）
        </li>
      )}
    </ul>
  );
}

type WorkRow = Schemas["WorkRow"];

const problemColumns: ColumnDef<WorkRow, unknown>[] = [
  {
    header: "状态",
    size: 90,
    cell: ({ row }) => <StatusOf labels={workStatus} value={row.original.work.status} />,
  },
  {
    header: "达人 / 作品",
    size: 240,
    cell: ({ row }) => (
      <div className="min-w-0">
        <div className="truncate">{row.original.creator.name}</div>
        <div className="text-muted-foreground font-mono text-[11px]">
          第 {row.original.creator.position} 位 · #{row.original.work.position}
        </div>
      </div>
    ),
  },
  {
    header: "原因",
    cell: ({ row }) => <span className="line-clamp-2 text-xs">{row.original.work.error}</span>,
  },
  {
    header: "更新",
    size: 100,
    cell: ({ row }) => (
      <span className="text-muted-foreground text-xs">{ago(row.original.work.updated_at)}</span>
    ),
  },
];

/** 失败和待核查的视频，点一行进入视频详情处理。 */
function Problems({ batchId, filters }: { batchId: string; filters: Filters }) {
  const navigate = useNavigate();
  const { page, set } = filters;
  const query = $api.useQuery("get", "/api/works", {
    params: { query: { batch_id: batchId, status: PROBLEMS, page, size: SIZE } },
  });
  return (
    <Panel>
      <QueryState query={query} isEmpty={(data) => !data.total} empty="没有失败或待核查的视频">
        {(data) => (
          <>
            <DataTable
              columns={problemColumns}
              data={data.items}
              onRowClick={(row) => navigate(`/works/${row.work.id}`)}
            />
            <Pager
              page={page}
              size={SIZE}
              total={data.total}
              onPage={(next) => set({ page: next })}
            />
          </>
        )}
      </QueryState>
    </Panel>
  );
}
