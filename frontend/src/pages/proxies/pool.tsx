import type { ColumnDef, OnChangeFn, RowSelectionState } from "@tanstack/react-table";
import {
  ChevronDown,
  FlaskConical,
  FolderInput,
  Gauge,
  GripVertical,
  Pencil,
  Power,
  PowerOff,
  RefreshCw,
  Search,
  Tags,
  Trash2,
  X,
} from "lucide-react";
import type { DragEvent } from "react";
import { $api, type Schemas } from "@/api/client";
import { Confirm } from "@/components/confirm";
import { DataTable, Pager } from "@/components/data-table";
import { Panel, QueryState } from "@/components/page";
import { Status } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ago } from "@/lib/format";
import { useFilters } from "@/lib/params";
import { useDone } from "@/lib/refresh";
import { proxyProtocols, proxyStatus, type Tone } from "@/lib/status";
import { cn } from "@/lib/utils";
import { ProxyDialog } from "./dialogs";
import {
  address,
  depths,
  PROXY_DRAG,
  proxyName,
  type Tag,
  UNFILED,
  useFolders,
  useUses,
} from "./data";
import { Recent, UseStates } from "./shared";

type Proxy = Schemas["ProxyView"];
type Action = Schemas["ProxyAction"];

const FILTERS = [
  "q",
  "folder",
  "use",
  "line",
  "protocol",
  "status",
  "enabled",
  "ip_version",
  "dynamic",
] as const;
const SIZE = 50;
const ALL = "all";

const flag = (value: string | undefined) => (value === undefined ? undefined : value === "true");

const DONE: Record<Action, (count: number) => string> = {
  test: (count) => `已安排测试 ${count} 个代理`,
  speed: (count) =>
    count
      ? `已安排测速 ${count} 个代理：每台设备一次测一个，每个约半分钟`
      : "选中的代理还没测试通过，先测试再测速",
  refresh: (count) =>
    count ? `${count} 个动态代理用完当前任务后刷新出口` : "选中的代理都没有填刷新地址",
  enable: (count) => `已启用 ${count} 个代理`,
  disable: (count) => `已停用 ${count} 个代理`,
  delete: (count) => `已删除 ${count} 个代理`,
};

/** 拖动时跟着鼠标的小标签，写明拖了几个代理。 */
function dragLabel(event: DragEvent, text: string) {
  const label = Object.assign(document.createElement("div"), { textContent: text });
  label.className = "bg-foreground text-background fixed -top-20 rounded-md px-2 py-1 text-xs";
  document.body.append(label);
  event.dataTransfer.setDragImage(label, -8, -8);
  requestAnimationFrame(() => label.remove());
}

const columns: ColumnDef<Proxy, unknown>[] = [
  {
    id: "select",
    size: 56,
    header: ({ table }) => (
      <Checkbox
        aria-label="选择本页"
        className="ml-5"
        checked={
          table.getIsAllPageRowsSelected() || (table.getIsSomePageRowsSelected() && "indeterminate")
        }
        onCheckedChange={(on) => table.toggleAllPageRowsSelected(on === true)}
      />
    ),
    cell: ({ row, table }) => (
      <div className="flex items-center gap-1">
        <span
          draggable
          title="拖到左侧文件夹；勾选了多个时一起移动"
          className="text-muted-foreground hover:text-foreground cursor-grab active:cursor-grabbing"
          onDragStart={(event) => {
            const ids = row.getIsSelected() ? Object.keys(table.getState().rowSelection) : [row.id];
            event.dataTransfer.setData(PROXY_DRAG, JSON.stringify(ids));
            event.dataTransfer.effectAllowed = "move";
            dragLabel(event, ids.length > 1 ? `${ids.length} 个代理` : proxyName(row.original));
          }}
        >
          <GripVertical className="size-4" />
        </span>
        <Checkbox
          aria-label={`选择 ${proxyName(row.original)}`}
          checked={row.getIsSelected()}
          onCheckedChange={(on) => row.toggleSelected(on === true)}
        />
      </div>
    ),
  },
  {
    header: "代理",
    cell: ({ row }) => <ProxyCell proxy={row.original} />,
  },
  {
    header: "出口",
    cell: ({ row }) =>
      row.original.exit_ip ? (
        <span className="flex items-center gap-1.5 font-mono text-xs">
          {row.original.exit_ip}
          {row.original.ip_version === 6 && <Badge variant="secondary">IPv6</Badge>}
        </span>
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
  },
  {
    header: "用途",
    cell: ({ row }) => (
      <UseStates
        states={row.original.states}
        idle={!row.original.enabled || row.original.status !== "available"}
      />
    ),
  },
  { header: "状态", cell: ({ row }) => <ProxyState proxy={row.original} /> },
  {
    header: "延迟 · 速度",
    size: 112,
    cell: ({ row }) => (
      <div className="space-y-0.5 font-mono text-xs">
        <div>{row.original.latency_ms === null ? "—" : `${row.original.latency_ms} ms`}</div>
        <Speed proxy={row.original} />
      </div>
    ),
  },
  {
    header: "使用",
    size: 80,
    cell: ({ row }) => (
      <div className="space-y-0.5 font-mono text-xs">
        <div title="整个集群正在用它的任务数">在用 {row.original.in_use}</div>
        <Recent ok={row.original.recent_ok} failed={row.original.recent_failed} />
      </div>
    ),
  },
  {
    id: "edit",
    size: 44,
    cell: ({ row }) => (
      <ProxyDialog
        proxy={row.original}
        trigger={
          <Button variant="ghost" size="icon-sm" aria-label="编辑">
            <Pencil />
          </Button>
        }
      />
    ),
  },
];

export function Pool({
  selection,
  onSelectionChange,
}: {
  selection: RowSelectionState;
  onSelectionChange: OnChangeFn<RowSelectionState>;
}) {
  const { values, page, set } = useFilters(FILTERS);
  const selected = Object.keys(selection);
  const clear = () => onSelectionChange({});
  const uses = useUses().data ?? [];
  const folders = useFolders().data?.folders ?? [];
  const query = $api.useQuery(
    "get",
    "/api/proxies",
    {
      params: {
        query: {
          q: values.q,
          folder_id: values.folder === UNFILED ? undefined : values.folder,
          unfiled: values.folder === UNFILED,
          use: values.use as Schemas["Use"] | undefined,
          line: values.line,
          protocol: values.protocol as Schemas["ProxyProtocol"] | undefined,
          status: values.status as Schemas["ProxyStatus"] | undefined,
          enabled: flag(values.enabled),
          ip_version: values.ip_version ? Number(values.ip_version) : undefined,
          dynamic: flag(values.dynamic),
          page,
          size: SIZE,
        },
      },
    },
    { refetchInterval: 10_000 },
  );
  const done = useDone();
  const finish = (message: string) => {
    done(message);
    clear();
  };
  const act = $api.useMutation("post", "/api/proxies/{action}");
  const run = (action: Action) =>
    act.mutate(
      { params: { path: { action } }, body: { ids: selected } },
      { onSuccess: (result) => finish(DONE[action](result.count)) },
    );

  const place =
    values.folder === UNFILED
      ? "未分组"
      : (folders.find((folder) => folder.id === values.folder)?.name ?? "全部代理");
  const filtered = FILTERS.some((key) => key !== "folder" && values[key]);
  return (
    <Panel className="rise min-w-0">
      <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
        <h3 className="mr-1 text-sm font-semibold">{place}</h3>
        <div className="relative">
          <Search className="text-muted-foreground absolute top-2 left-2.5 size-4" />
          <Input
            key={values.q}
            defaultValue={values.q}
            placeholder="搜主机、备注、出口 IP"
            aria-label="搜索代理"
            className="h-8 w-56 pl-8"
            onKeyDown={(event) =>
              event.key === "Enter" && set({ q: event.currentTarget.value.trim() })
            }
          />
        </div>
        <FilterSelect
          value={values.use}
          all="全部用途"
          options={uses.map((use) => [use.key, use.name])}
          onChange={(use) => set({ use })}
        />
        <FilterSelect
          value={values.status}
          all="全部状态"
          options={Object.entries(proxyStatus).map(([key, [label]]) => [key, label])}
          onChange={(status) => set({ status })}
        />
        <FilterSelect
          value={values.protocol}
          all="全部协议"
          options={Object.entries(proxyProtocols)}
          onChange={(protocol) => set({ protocol })}
        />
        <FilterSelect
          value={values.enabled}
          all="启用和停用"
          options={[
            ["true", "已启用"],
            ["false", "已停用"],
          ]}
          onChange={(enabled) => set({ enabled })}
        />
        <FilterSelect
          value={values.ip_version}
          all="IPv4 和 IPv6"
          options={[
            ["4", "IPv4"],
            ["6", "IPv6"],
          ]}
          onChange={(ip_version) => set({ ip_version })}
        />
        <FilterSelect
          value={values.dynamic}
          all="静态和动态"
          options={[
            ["false", "静态"],
            ["true", "动态"],
          ]}
          onChange={(dynamic) => set({ dynamic })}
        />
        {values.line && (
          <Button size="xs" variant="secondary" onClick={() => set({ line: undefined })}>
            线路 <span className="font-mono">{values.line}</span> <X />
          </Button>
        )}
      </div>
      {selected.length > 0 && (
        <div className="bg-signal/10 flex flex-wrap items-center gap-1.5 border-b px-3 py-2 text-sm">
          <span className="mr-2">
            已选 <span className="font-mono font-semibold">{selected.length}</span> 个
          </span>
          <Button size="xs" variant="outline" onClick={() => run("test")}>
            <FlaskConical /> 测试
          </Button>
          <Button size="xs" variant="outline" onClick={() => run("speed")}>
            <Gauge /> 测速
          </Button>
          <Button size="xs" variant="outline" onClick={() => run("refresh")}>
            <RefreshCw /> 刷新出口
          </Button>
          <Button size="xs" variant="outline" onClick={() => run("enable")}>
            <Power /> 启用
          </Button>
          <Button size="xs" variant="outline" onClick={() => run("disable")}>
            <PowerOff /> 停用
          </Button>
          <UsesMenu ids={selected} onDone={finish} />
          <MoveMenu ids={selected} onDone={finish} />
          <Confirm
            title="删除代理"
            description={`删除选中的 ${selected.length} 个代理，之后不再分配。`}
            action="删除"
            onConfirm={() => run("delete")}
          >
            <Button size="xs" variant="outline" className="text-status-failed">
              <Trash2 /> 删除
            </Button>
          </Confirm>
          <Button size="xs" variant="ghost" className="ml-auto" onClick={clear}>
            取消选择
          </Button>
        </div>
      )}
      <QueryState
        query={query}
        rows={6}
        isEmpty={(data) => !data.items.length}
        empty={
          filtered
            ? "没有符合条件的代理"
            : values.folder
              ? "这里还没有代理：把代理拖进来，或导入时选这个文件夹"
              : "代理池是空的，点右上角“导入”或“添加代理”"
        }
      >
        {(data) => (
          <>
            <DataTable
              columns={columns}
              data={data.items}
              getRowId={(proxy) => proxy.id}
              selection={selection}
              onSelectionChange={onSelectionChange}
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

/** 名称在上；下面一行是协议、地址、用户名、是否动态和所在文件夹。 */
function ProxyCell({ proxy }: { proxy: Proxy }) {
  const folder = useFolders().data?.folders.find((item) => item.id === proxy.folder_id);
  const where = address(proxy.host, proxy.port);
  return (
    <div className="min-w-0 space-y-0.5">
      <div className={cn("font-medium", !proxy.name && "font-mono text-xs")}>
        {proxy.name || where}
      </div>
      <div className="text-muted-foreground font-mono text-xs">
        {[
          proxyProtocols[proxy.protocol],
          proxy.name && where,
          proxy.username && `用户 ${proxy.username}`,
          proxy.dynamic && "动态",
          folder && `文件夹 ${folder.name}`,
        ]
          .filter(Boolean)
          .join(" · ")}
      </div>
    </div>
  );
}

/** 代理本身的状态；它在各用途上的线路状态看“用途”一列。 */
function ProxyState({ proxy }: { proxy: Proxy }) {
  const [label, tone]: [string, Tone] = proxy.enabled
    ? proxyStatus[proxy.status]
    : ["已停用", "muted"];
  const note = proxy.testing ? "等待爬取程序测试" : proxy.error;
  return (
    <div className="space-y-0.5">
      <Status label={label} tone={tone} />
      {note && (
        <p className="text-muted-foreground max-w-40 truncate text-xs" title={note}>
          {note}
        </p>
      )}
    </div>
  );
}

/** 测速结果“单路 / 并发 Mbps”：单路下载速度和几路同时下载的合计；悬停看 MB/s 和测速时间。 */
function Speed({ proxy }: { proxy: Proxy }) {
  const { speed_mbps: single, parallel_mbps: parallel, speed_error: error } = proxy;
  if (proxy.speed_testing) return <div className="text-muted-foreground font-sans">测速中…</div>;
  if (error) {
    return (
      <p
        className="text-status-failed max-w-28 truncate font-sans"
        title={`${ago(proxy.speed_tested_at)}测速：${error}`}
      >
        {error}
      </p>
    );
  }
  if (single === null || parallel === null) return null;
  const bytes = (mbps: number) => `${(mbps / 8).toFixed(1)} MB/s`;
  const title = `${ago(proxy.speed_tested_at)}测速：单路 ${single} Mbps（${bytes(single)}），多路同时下载合计 ${parallel} Mbps（${bytes(parallel)}）`;
  return (
    <div title={title}>
      {Math.round(single)} / {Math.round(parallel)} Mbps
    </div>
  );
}

function FilterSelect({
  value,
  all,
  options,
  onChange,
}: {
  value: string | undefined;
  all: string;
  options: [string, string][];
  onChange: (value: string | undefined) => void;
}) {
  return (
    <Select
      value={value ?? ALL}
      onValueChange={(next) => onChange(next === ALL ? undefined : next)}
    >
      <SelectTrigger size="sm" aria-label={all} className={cn(value && "border-foreground/40")}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>{all}</SelectItem>
        {options.map(([key, label]) => (
          <SelectItem key={key} value={key}>
            {label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** 给选中的代理加上或去掉一个用途；选整个服务表示它的所有任务。 */
function UsesMenu({ ids, onDone }: { ids: string[]; onDone: (message: string) => void }) {
  const uses = useUses().data ?? [];
  const retag = $api.useMutation("post", "/api/proxies/uses");
  const tags: [Tag, string][] = [
    ...new Map(uses.map((use) => [use.service, `${use.service_name}（全部任务）`])),
    ...uses.map((use): [Tag, string] => [use.key, `${use.service_name} · ${use.name}`]),
  ];
  const change = (tag: Tag, name: string, add: boolean) =>
    retag.mutate(
      { body: add ? { ids, add: [tag] } : { ids, remove: [tag] } },
      {
        onSuccess: (result) => onDone(`${result.count} 个代理已${add ? "加上" : "去掉"}“${name}”`),
      },
    );
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="xs" variant="outline">
          <Tags /> 用途 <ChevronDown />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>加上用途</DropdownMenuLabel>
        {tags.map(([tag, name]) => (
          <DropdownMenuItem key={tag} onSelect={() => change(tag, name, true)}>
            {name}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuLabel>去掉用途</DropdownMenuLabel>
        {tags.map(([tag, name]) => (
          <DropdownMenuItem key={tag} onSelect={() => change(tag, name, false)}>
            {name}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** 把选中的代理移到某个文件夹；也可以直接拖到左侧文件夹上。 */
function MoveMenu({ ids, onDone }: { ids: string[]; onDone: (message: string) => void }) {
  const folders = useFolders().data?.folders ?? [];
  const depth = depths(folders);
  const move = $api.useMutation("post", "/api/proxies/move");
  const to = (folder_id: string | null, name: string) =>
    move.mutate(
      { body: { ids, folder_id } },
      { onSuccess: (result) => onDone(`已把 ${result.count} 个代理移到${name}`) },
    );
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="xs" variant="outline">
          <FolderInput /> 移动到 <ChevronDown />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuItem onSelect={() => to(null, "未分组")}>未分组</DropdownMenuItem>
        {folders.length > 0 && <DropdownMenuSeparator />}
        {folders.map((folder) => (
          <DropdownMenuItem key={folder.id} onSelect={() => to(folder.id, `“${folder.name}”`)}>
            <span style={{ paddingLeft: `${(depth.get(folder.id) ?? 0) * 0.9}rem` }}>
              {folder.name}
            </span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
