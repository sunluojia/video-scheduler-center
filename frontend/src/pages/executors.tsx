import type { ColumnDef } from "@tanstack/react-table";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { $ai, $api, type AnalysisSchemas, type Schemas } from "@/api/client";
import { IdentityAlerts, ProxyAlerts } from "@/components/alerts";
import { Confirm } from "@/components/confirm";
import { CrawlProgress } from "@/components/crawl-progress";
import { DataTable } from "@/components/data-table";
import { DownloadProgress } from "@/components/download-progress";
import { Field } from "@/components/field";
import { Facts, Metric, Notice, PageHeader, Panel, QueryState, Section } from "@/components/page";
import { Status } from "@/components/status";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ago, bytes, dateTime, shortTime } from "@/lib/format";
import { useDone } from "@/lib/refresh";
import { executorKinds, jobStatus, stageNames } from "@/lib/status";

type Executor = Schemas["ExecutorView"];

const executorRowId = (item: Executor) => `${item.kind}/${item.instance_id}`;

const registrationColumns: ColumnDef<Executor, unknown>[] = [
  {
    header: "状态",
    size: 80,
    cell: ({ row: { original: executor } }) => {
      if (executor.withdrawn_at !== null) return <Status label="已注销" tone="muted" />;
      if (executor.online) return <Status label="在线" tone="done" />;
      if (!executor.last_seen_at) return <Status label="待心跳" tone="attention" />;
      return <Status label="离线" tone="failed" />;
    },
  },
  { header: "类型", size: 100, cell: ({ row }) => executorKinds[row.original.kind] },
  {
    header: "实例",
    cell: ({ row }) => <span className="font-mono text-xs">{row.original.instance_id}</span>,
  },
  {
    header: "地址",
    cell: ({ row }) => <span className="font-mono text-xs">{row.original.url}</span>,
  },
  {
    header: "版本",
    size: 90,
    cell: ({ row }) => <span className="font-mono text-xs">{row.original.version || "—"}</span>,
  },
  {
    header: "最近心跳",
    size: 110,
    cell: ({ row }) => <span className="text-xs">{ago(row.original.last_seen_at)}</span>,
  },
  {
    header: "登记时间",
    size: 160,
    cell: ({ row }) => (
      <span className="text-muted-foreground font-mono text-xs">
        {dateTime(row.original.registered_at)}
      </span>
    ),
  },
];

const recentActivity: ColumnDef<Executor, unknown> = {
  header: "过去 24 小时",
  size: 170,
  cell: ({ row: { original } }) => (
    <span className="text-xs">
      完成 {original.activity?.completed_24h ?? 0}
      {original.kind === "crawler" && (
        <>
          {" "}
          · 过期 {original.activity?.expired_24h ?? 0} · 接手{" "}
          {original.activity?.takeovers_24h ?? 0}
        </>
      )}
    </span>
  ),
};

const activeColumns: ColumnDef<Executor, unknown>[] = [
  ...registrationColumns,
  {
    header: "当前任务",
    size: 105,
    cell: ({ row: { original } }) => (
      <span className="text-xs">
        {original.activity?.active ?? 0} 个{original.kind === "crawler" ? "认领" : "阶段"}
      </span>
    ),
  },
  recentActivity,
  {
    header: "任务并发",
    size: 215,
    cell: ({ row }) =>
      row.original.kind === "crawler" ? (
        <SlotsDialog executor={row.original} />
      ) : (
        <Button asChild size="xs" variant="outline">
          <Link to="/ai?tab=models">
            <Pencil /> 模型并发
          </Link>
        </Button>
      ),
  },
  {
    header: "操作",
    size: 90,
    cell: ({ row }) => (row.original.online ? null : <RemoveExecutor executor={row.original} />),
  },
];

const historyColumns: ColumnDef<Executor, unknown>[] = [
  ...registrationColumns,
  {
    header: "注销时间",
    size: 160,
    cell: ({ row: { original } }) => (
      <time
        className="text-muted-foreground font-mono text-xs"
        dateTime={original.withdrawn_at ?? undefined}
      >
        {dateTime(original.withdrawn_at)}
      </time>
    ),
  },
  recentActivity,
];

export function ExecutorsPage() {
  const executors = $api.useQuery("get", "/api/executors", {}, { refetchInterval: 15_000 });
  return (
    <>
      <PageHeader
        eyebrow="Executors"
        title="执行程序"
        meta="爬取程序和分析服务启动后向调度中心注册，每 15 秒心跳一次，超过 60 秒没有心跳视为离线。"
      />
      <ProxyAlerts />
      <IdentityAlerts />
      <Section title="实例" extra={<AddExecutor />}>
        <Panel>
          <QueryState query={executors} isEmpty={(data) => !data.length} empty="还没有执行程序">
            {(data) => <ExecutorInventory executors={data} />}
          </QueryState>
        </Panel>
      </Section>
      <div className="grid gap-8 xl:grid-cols-2">
        <AnalysisStatus />
        <CrawlerStatus />
      </div>
    </>
  );
}

function ExecutorInventory({ executors }: { executors: Executor[] }) {
  const active = executors.filter((item) => item.withdrawn_at === null);
  const history = executors.filter((item) => item.withdrawn_at !== null);
  return (
    <>
      {active.length ? (
        <DataTable columns={activeColumns} data={active} getRowId={executorRowId} />
      ) : (
        <Notice>暂无在册执行程序</Notice>
      )}
      {history.length > 0 && (
        <details className="border-t">
          <summary className="text-muted-foreground cursor-pointer px-4 py-3 text-sm">
            历史设备（{history.length}）
          </summary>
          <DataTable columns={historyColumns} data={history} getRowId={executorRowId} />
        </details>
      )}
    </>
  );
}

function AddExecutor() {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<Schemas["ExecutorKind"]>("crawler");
  const done = useDone();
  const add = $api.useMutation("post", "/api/executors", {
    onSuccess: () => {
      done("已添加执行程序，等待首次心跳");
      setOpen(false);
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    add.mutate({
      body: {
        kind,
        instance_id: String(form.get("instance_id") ?? "").trim(),
        url: String(form.get("url") ?? "").trim(),
      },
    });
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) setKind("crawler");
        setOpen(next);
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm" className="bg-signal text-signal-foreground hover:bg-signal/90">
          <Plus /> 添加执行程序
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>添加执行程序</DialogTitle>
          <DialogDescription>
            程序在目标机器启动后，使用相同的实例 ID 发送心跳即可联机。
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={submit}>
          <Field label="类型">
            <Select value={kind} onValueChange={(value) => setKind(value as typeof kind)}>
              <SelectTrigger className="w-full" aria-label="类型">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="crawler">{executorKinds.crawler}</SelectItem>
                <SelectItem value="analysis">{executorKinds.analysis}</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <Field label="实例 ID">
            <Input name="instance_id" required maxLength={128} className="font-mono" />
          </Field>
          <Field label="访问地址" hint="填写调度中心能访问的 HTTP 或 HTTPS 地址。">
            <Input
              name="url"
              type="url"
              required
              maxLength={512}
              placeholder={kind === "crawler" ? "http://127.0.0.1:8091" : "http://127.0.0.1:8001"}
              className="font-mono"
            />
          </Field>
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="outline">
                取消
              </Button>
            </DialogClose>
            <Button type="submit" disabled={add.isPending}>
              添加
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** 采集、下载和预热并发随下一次心跳下发。 */
function SlotsDialog({ executor }: { executor: Executor }) {
  const [open, setOpen] = useState(false);
  const done = useDone();
  const save = $api.useMutation("put", "/api/executors/crawler/{instance_id}/slots", {
    onSuccess: () => {
      done(`已保存，${executor.instance_id} 下一次心跳（15 秒内）按新的并发运行`);
      setOpen(false);
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    save.mutate({
      params: { path: { instance_id: executor.instance_id } },
      body: {
        crawl_slots: Number(form.get("crawl_slots")),
        download_slots: Number(form.get("download_slots")),
        warm_slots: Number(form.get("warm_slots")),
      },
    });
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="ghost" className="h-7 px-2 font-mono text-xs">
          采集 {executor.crawl_slots} · 下载 {executor.download_slots} · 预热{" "}
          {executor.warm_slots ?? 1}
          <Pencil className="size-3" />
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>并发</DialogTitle>
          <DialogDescription className="font-mono">{executor.instance_id}</DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={submit}>
          <Field
            label="同时采集达人"
            hint="这台设备同时采集的达人数，每个任务使用一个 Chrome 窗口。实际并发还受「获取信息」可用线路的名额限制。"
          >
            <Input
              name="crawl_slots"
              type="number"
              min={0}
              max={16}
              defaultValue={executor.crawl_slots}
              required
            />
          </Field>
          <Field
            label="同时下载"
            hint="这台设备同时下载的视频数，还受「下载视频」可用线路的名额限制。"
          >
            <Input
              name="download_slots"
              type="number"
              min={0}
              max={80}
              defaultValue={executor.download_slots}
              required
            />
          </Field>
          <Field label="同时预热账号" hint="这台设备同时预热的身份数，使用「获取信息」线路。">
            <Input
              name="warm_slots"
              type="number"
              min={0}
              max={16}
              defaultValue={executor.warm_slots ?? 1}
              required
            />
          </Field>
          <DialogFooter>
            <Button type="submit" disabled={save.isPending}>
              保存
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function RemoveExecutor({ executor }: { executor: Executor }) {
  const done = useDone();
  const remove = $api.useMutation("delete", "/api/executors/{kind}/{instance_id}", {
    onSuccess: () => done("已删除执行程序记录"),
  });
  return (
    <Confirm
      title="删除执行程序"
      description={`将删除 ${executor.instance_id} 的登记记录。程序再次发送心跳时会重新注册。`}
      action="删除"
      onConfirm={() =>
        remove.mutate({
          params: { path: { kind: executor.kind, instance_id: executor.instance_id } },
        })
      }
    >
      <Button
        type="button"
        size="xs"
        variant="ghost"
        className="text-status-failed"
        disabled={remove.isPending}
      >
        <Trash2 /> 删除
      </Button>
    </Confirm>
  );
}

function AnalysisStatus() {
  const query = $api.useQuery("get", "/api/executors/{kind}/status", {
    params: { path: { kind: "analysis" } },
  });
  const models = $ai.useQuery("get", "/api/ai/models");
  const modelName = (id: string) =>
    models.data?.find((model) => model.id === id)?.name ?? id.slice(0, 8);
  return (
    <Section
      title="分析服务"
      extra={
        <Button asChild variant="outline" size="sm">
          <Link to="/ai?tab=models">
            <Pencil /> 设置模型并发
          </Link>
        </Button>
      }
    >
      <details className="text-muted-foreground text-xs">
        <summary className="cursor-pointer">分析 Worker 的部署并发设置</summary>
        <p className="mt-2">
          在分析服务部署环境中设置下载并发 <code>VA_DOWNLOAD_SLOTS</code>、媒体处理并发{" "}
          <code>VA_MEDIA_SLOTS</code> 和后台线程池大小 <code>VA_THREAD_POOL_SIZE</code>。 Worker
          在启动时读取这些值。
        </p>
      </details>
      <Panel>
        <QueryState query={query}>
          {(raw) => {
            const status = raw as AnalysisSchemas["ServiceStatus"];
            const stages = Object.keys({ ...status.ready, ...status.running });
            return (
              <div className="space-y-5 p-5">
                <div className="grid grid-cols-3 gap-6">
                  <Metric label="视频任务处理中" value={status.jobs.video?.processing ?? 0} />
                  <Metric
                    label="待核查"
                    value={
                      (status.jobs.video?.needs_review ?? 0) +
                      (status.jobs.summary?.needs_review ?? 0)
                    }
                  />
                  <Metric label="剩余磁盘" value={bytes(status.free_disk_bytes)} />
                </div>
                <table className="w-full text-sm">
                  <thead className="text-muted-foreground text-xs">
                    <tr className="border-b">
                      <th className="py-2 text-left font-normal">阶段</th>
                      <th className="py-2 text-right font-normal">等待</th>
                      <th className="py-2 text-right font-normal">执行中</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono">
                    {stages.map((stage) => (
                      <tr key={stage} className="border-b last:border-0">
                        <td className="py-1.5 font-sans">{stageNames[stage] ?? stage}</td>
                        <td className="py-1.5 text-right">
                          {status.ready[stage as keyof typeof status.ready] ?? 0}
                        </td>
                        <td className="py-1.5 text-right">
                          {status.running[stage as keyof typeof status.running] ?? 0}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {status.models.map((model) => (
                  <div key={model.model_id} className="space-y-1">
                    <div className="flex justify-between text-xs">
                      <span className="font-mono">{modelName(model.model_id)}</span>
                      <span className="text-muted-foreground">
                        并发 {model.running}/{model.capacity} · 每分钟 {model.recent_requests}/
                        {model.requests_per_minute}
                      </span>
                    </div>
                    <div className="bg-muted h-1.5 overflow-hidden rounded-full">
                      <div
                        className="bg-status-active h-full"
                        style={{ width: `${(model.running / model.capacity) * 100}%` }}
                      />
                    </div>
                  </div>
                ))}
                <Facts
                  items={Object.entries(status.jobs).flatMap(([kind, counts]) =>
                    Object.entries(counts).map(([state, count]): [string, string] => [
                      `${kind === "video" ? "视频" : "汇总"} · ${jobStatus[state as Schemas["JobStatus"]]?.[0] ?? state}`,
                      String(count),
                    ]),
                  )}
                />
              </div>
            );
          }}
        </QueryState>
      </Panel>
    </Section>
  );
}

type Jobs = { running: number; queued: number };

/** 爬取程序 GET /api/status 的内容，约定见设计文档第 4.4 节。 */
type CrawlerState = {
  crawl: Jobs;
  download: Jobs;
  /** 模拟爬取程序没有身份池。 */
  identities: { target: number; ready: number } | null;
  leases: number;
  paused_batches: string[];
  /** 下载连续失败时整台设备暂停到这个时间。 */
  download_paused_until: string | null;
};

function CrawlerStatus() {
  const query = $api.useQuery("get", "/api/executors/{kind}/status", {
    params: { path: { kind: "crawler" } },
  });
  return (
    <Section title="爬取程序">
      <Panel>
        <QueryState query={query}>
          {(raw) => {
            const status = raw as CrawlerState;
            const { identities } = status;
            const short = identities && identities.ready < identities.target;
            return (
              <div className="space-y-5 p-5">
                <div className="grid grid-cols-3 gap-6">
                  <Metric label="达人采集进行中" value={status.crawl.running} />
                  <Metric label="下载进行中" value={status.download.running} />
                  <Metric
                    label="可用身份"
                    tone={short ? "text-status-attention" : undefined}
                    value={
                      identities ? (
                        <Link to="/identities" className="hover:underline">
                          {identities.ready}
                          <span className="text-muted-foreground text-base">
                            {" "}
                            / {identities.target}
                          </span>
                        </Link>
                      ) : (
                        "—"
                      )
                    }
                  />
                </div>
                <Facts
                  items={[
                    ["排队", `获取作品 ${status.crawl.queued} · 下载 ${status.download.queued}`],
                    ["正在用的线路", status.leases],
                    [
                      "暂停的批次",
                      status.paused_batches.length ? `${status.paused_batches.length} 个` : "无",
                    ],
                    [
                      "下载",
                      status.download_paused_until ? (
                        <span className="text-status-attention">
                          连续失败，暂停到 {shortTime(status.download_paused_until)}
                        </span>
                      ) : (
                        "正常"
                      ),
                    ],
                  ]}
                />
              </div>
            );
          }}
        </QueryState>
      </Panel>
      <Panel className="space-y-3 p-5">
        <CrawlProgress />
      </Panel>
      <Panel className="p-5">
        <DownloadProgress title="达人视频下载" />
      </Panel>
    </Section>
  );
}
