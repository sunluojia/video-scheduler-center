import type { ColumnDef } from "@tanstack/react-table";
import { SlidersHorizontal } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useIdentities } from "@/api/alerts";
import { $api, type Schemas } from "@/api/client";
import { IdentityAlerts } from "@/components/alerts";
import { Confirm } from "@/components/confirm";
import { DataTable } from "@/components/data-table";
import { Field } from "@/components/field";
import { Metric, Notice, PageHeader, Panel, QueryState, Section } from "@/components/page";
import { SegmentBar, Status, StatusOf } from "@/components/status";
import { Button } from "@/components/ui/button";
import {
  Dialog,
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
import { ago, shortTime } from "@/lib/format";
import { useDone } from "@/lib/refresh";
import { identityState } from "@/lib/status";
import { cn } from "@/lib/utils";
import { Segmented } from "@/pages/proxies/shared";

type Device = Schemas["IdentityDevice"];
type Row = Schemas["Identity"] & { device: string; online: boolean };
type Shown = "pool" | "retired" | "all";

/** 账号池：各台爬取程序的身份池，数据来自它们每 15 秒的心跳。 */
export function IdentitiesPage() {
  const query = useIdentities();
  return (
    <>
      <PageHeader
        eyebrow="Identities"
        title="账号池"
        meta="每个身份是爬取程序本机的一个浏览器资料目录，是未登录的访客，不是平台账号，用来拿 Cookie 和下载要的 token。可用的少于设置的数量就自动补充，快过期就刷新，下载连续被拒就换掉；多台爬取程序平均分摊。Cookie 和 token 不离开设备。"
        actions={query.data && <PoolSettings target={query.data.target} />}
      />
      <IdentityAlerts />
      <QueryState
        query={query}
        rows={2}
        isEmpty={(board) => !board.devices.length}
        empty="还没有爬取程序报来身份池"
      >
        {(board) => (
          <>
            <div className="rise grid gap-4 xl:grid-cols-2">
              {board.devices.map((device) => (
                <DeviceCard key={device.instance_id} device={device} />
              ))}
            </div>
            <IdentityTable devices={board.devices} />
          </>
        )}
      </QueryState>
    </>
  );
}

/** 账号池要保持几个可用；爬取程序每次心跳拿到自己分到的数量，后台自动补充。 */
function PoolSettings({ target }: { target: number }) {
  const [open, setOpen] = useState(false);
  const done = useDone();
  const save = $api.useMutation("put", "/api/identity-pool", {
    onSuccess: (setting) => {
      done(`已保存：至少保持 ${setting.target} 个可用，爬取程序 15 秒内按新数量补充`);
      setOpen(false);
    },
  });
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    save.mutate({ body: { target: Number(new FormData(event.currentTarget).get("target")) } });
  };
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline">
          <SlidersHorizontal /> 至少保持 {target} 个可用
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>账号池数量</DialogTitle>
          <DialogDescription>
            可用的少于这个数，后台就新建并预热补上；多台爬取程序时平均分摊，每台最多留分到数量的两倍。
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <Field
            label="至少保持可用"
            hint="一台爬取程序同时做 2 个获取作品、4 个下载，保持 8 个左右够用。"
          >
            <Input
              name="target"
              type="number"
              min={1}
              max={200}
              defaultValue={target}
              required
              autoFocus
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

const BAR: Schemas["IdentityState"][] = ["ready", "in_use", "warming", "cooling", "stale"];

function DeviceCard({ device }: { device: Device }) {
  const { snapshot } = device;
  const short = snapshot.ready < snapshot.target;
  const segments = BAR.map((state) => ({
    status: state,
    label: identityState[state][0],
    tone: identityState[state][1],
    count: snapshot.items.filter((item) => item.state === state).length,
  }));
  return (
    <Panel className="space-y-4 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-0.5">
          <div className="text-muted-foreground text-xs">设备</div>
          <h3 className="font-mono text-sm font-semibold">{device.instance_id}</h3>
        </div>
        <span className="flex items-center gap-3 text-xs">
          <span className="text-muted-foreground">心跳 {ago(device.last_seen_at)}</span>
          <Status
            label={device.online ? "在线" : "离线"}
            tone={device.online ? "done" : "failed"}
          />
        </span>
      </div>
      <div className="grid grid-cols-4 gap-4">
        <Metric
          label="可用 / 分到"
          tone={short ? "text-status-attention" : undefined}
          value={
            <>
              {snapshot.ready}
              <span className="text-muted-foreground text-base"> / {snapshot.target}</span>
            </>
          }
        />
        <Metric label="使用中" value={snapshot.in_use} />
        <Metric label="预热中" value={snapshot.warming} />
        <Metric label="冷却中" value={snapshot.cooling} />
      </div>
      <SegmentBar segments={segments} />
      <div className="text-muted-foreground flex flex-wrap gap-x-5 gap-y-1 border-t pt-3 text-xs">
        <span>近一小时</span>
        <Rate label="预热失败" bad={snapshot.warm_failed} total={snapshot.warm_total} />
        <Rate label="作品列表空内容" bad={snapshot.list_empty} total={snapshot.list_total} />
        <Rate label="下载被拒" bad={snapshot.download_blocked} total={snapshot.download_total} />
      </div>
      {short && snapshot.short_since && (
        <p className="text-status-attention text-xs">
          可用身份从{ago(snapshot.short_since)}开始少于目标，后台在补
        </p>
      )}
    </Panel>
  );
}

/** 坏的次数占比，悬停看次数。 */
function Rate({ label, bad, total }: { label: string; bad: number; total: number }) {
  return (
    <span title={`${bad} / ${total} 次`}>
      {label}{" "}
      <b className={cn("font-mono", bad ? "text-status-failed" : "text-foreground")}>
        {total ? `${Math.round((bad / total) * 100)}%` : "—"}
      </b>
    </span>
  );
}

function IdentityTable({ devices }: { devices: Device[] }) {
  const [device, setDevice] = useState("all");
  const [shown, setShown] = useState<Shown>("pool");
  const rows: Row[] = devices
    .filter((item) => device === "all" || item.instance_id === device)
    .flatMap((item) =>
      item.snapshot.items.map((identity) => ({
        ...identity,
        device: item.instance_id,
        online: item.online,
      })),
    )
    .filter((row) => shown === "all" || (shown === "retired") === (row.state === "retired"));
  return (
    <Section
      title="身份"
      extra={
        <div className="flex items-center gap-2">
          {devices.length > 1 && (
            <Select value={device} onValueChange={setDevice}>
              <SelectTrigger size="sm" className="w-56" aria-label="设备">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部设备</SelectItem>
                {devices.map((item) => (
                  <SelectItem key={item.instance_id} value={item.instance_id}>
                    {item.instance_id}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Segmented
            value={shown}
            onChange={setShown}
            options={[
              ["pool", "在池里"],
              ["retired", "已退休"],
              ["all", "全部"],
            ]}
          />
        </div>
      }
    >
      <Panel>
        {rows.length ? (
          <DataTable columns={columns} data={rows} getRowId={(row) => `${row.device}/${row.id}`} />
        ) : (
          <Notice>没有符合条件的身份</Notice>
        )}
      </Panel>
    </Section>
  );
}

const mono = (text: string | null | undefined) => (
  <span className="font-mono text-xs">{text ?? "—"}</span>
);

const columns: ColumnDef<Row, unknown>[] = [
  { header: "设备", size: 180, cell: ({ row }) => mono(row.original.device) },
  { header: "编号", size: 130, cell: ({ row }) => mono(row.original.id) },
  {
    header: "状态",
    size: 90,
    cell: ({ row }) => <StatusOf labels={identityState} value={row.original.state} />,
  },
  {
    header: "连续失败",
    size: 80,
    cell: ({ row }) => (
      <span className={cn("font-mono text-xs", row.original.fails && "text-status-failed")}>
        {row.original.fails}
      </span>
    ),
  },
  {
    header: "token 到期",
    size: 110,
    cell: ({ row }) => <span className="text-xs">{ago(row.original.token_expires_at)}</span>,
  },
  {
    header: "预热",
    size: 110,
    cell: ({ row }) => <span className="text-xs">{ago(row.original.warmed_at)}</span>,
  },
  {
    header: "最近使用",
    size: 110,
    cell: ({ row }) => <span className="text-xs">{ago(row.original.last_used_at)}</span>,
  },
  { header: "Chrome", size: 70, cell: ({ row }) => mono(row.original.browser) },
  {
    header: "说明",
    cell: ({ row }) => <span className="text-muted-foreground text-xs">{note(row.original)}</span>,
  },
  { id: "actions", size: 130, cell: ({ row }) => <Actions row={row.original} /> },
];

function note(row: Row): string | null {
  if (row.state === "cooling") return `下载被拒，冷却到 ${shortTime(row.cooldown_until)}`;
  if (row.state === "retired") return `${ago(row.retired_at)}退休：${row.retire_reason ?? "—"}`;
  return null;
}

/** 刷新和退休由调度中心转给那台设备执行，页面在它下一次心跳后更新。 */
function Actions({ row }: { row: Row }) {
  const done = useDone();
  const act = $api.useMutation("post", "/api/identities/{instance_id}/{identity_id}/{action}");
  if (row.state === "retired") return null;
  const why = !row.online
    ? "设备离线"
    : row.state === "in_use" || row.state === "warming"
      ? "正在使用或预热，稍后再试"
      : undefined;
  const run = (action: Schemas["IdentityAction"], message: string) =>
    act.mutate(
      { params: { path: { instance_id: row.device, identity_id: row.id, action } } },
      { onSuccess: () => done(message) },
    );
  return (
    <div className="flex justify-end gap-1" title={why}>
      <Button
        size="sm"
        variant="ghost"
        disabled={Boolean(why) || act.isPending}
        onClick={() => run("refresh", `已让 ${row.id} 重新预热，下一次心跳后更新`)}
      >
        刷新
      </Button>
      <Confirm
        title={`退休身份 ${row.id}？`}
        description="删掉它的浏览器资料目录，后台会自动补一个新的。"
        action="退休"
        onConfirm={() => run("retire", `${row.id} 已退休，后台会补一个新的`)}
      >
        <Button size="sm" variant="ghost" disabled={Boolean(why) || act.isPending}>
          退休
        </Button>
      </Confirm>
    </div>
  );
}
