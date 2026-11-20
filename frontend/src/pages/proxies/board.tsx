import { useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Settings2 } from "lucide-react";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";
import { $api, type Schemas } from "@/api/client";
import { Field } from "@/components/field";
import { Panel, QueryState } from "@/components/page";
import { Status } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ago } from "@/lib/format";
import { useFilters } from "@/lib/params";
import { useDone } from "@/lib/refresh";
import { lineState, pickModes, ruleSources, toneClass } from "@/lib/status";
import { cn } from "@/lib/utils";
import { type UseCard, useFolders, useUses } from "./data";
import { FolderSelect, Recent, Segmented } from "./shared";

type Line = Schemas["LineView"];
type Rule = Schemas["RuleView"];
type Device = Schemas["DeviceRow"];
type Choice = "follow" | Schemas["RuleSource"];

/** 页面顶部：每个用途一张卡片，一眼看到它怎么走、线路好不好。 */
export function UseBoard() {
  const query = useUses();
  return (
    <QueryState query={query} rows={2}>
      {(uses) => (
        <div className="rise grid gap-4 xl:grid-cols-2">
          {uses.map((use) => (
            <Card key={use.key} use={use} />
          ))}
        </div>
      )}
    </QueryState>
  );
}

function Card({ use }: { use: UseCard }) {
  const { set } = useFilters(["line"] as const);
  const describe = useDescribe();
  const deviceLines = new Set(use.devices.map((device) => device.line?.key));
  // 设备换线路后，旧线路只显示到手头的任务结束。
  const lines = use.lines.filter(
    (line) => line.kind === "pool" || deviceLines.has(line.key) || line.in_use > 0,
  );
  const resting = lines.filter(resting_).length;
  const busy = lines.reduce((sum, line) => sum + line.in_use, 0);
  const ok = use.lines.reduce((sum, line) => sum + line.recent_ok, 0);
  const failed = use.lines.reduce((sum, line) => sum + line.recent_failed, 0);
  const exceptions = use.devices.filter((device) => device.rule);
  return (
    <Panel className="space-y-3 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-0.5">
          <div className="text-muted-foreground text-xs">{use.service_name}</div>
          <h3 className="font-semibold">{use.name}</h3>
        </div>
        <UseSettings use={use} />
      </div>
      <p className="text-sm">
        {describe(use.rule, true)}
        {use.rule.source === "pool" && (
          <span className="text-muted-foreground"> · 能用的代理 {use.proxies} 个</span>
        )}
      </p>
      <Lines lines={lines} onPick={(key) => set({ line: key })} />
      <div className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs">
        <span>
          线路 <b className="text-foreground font-mono">{lines.length}</b>
        </span>
        <span>
          冷却或限流{" "}
          <b className={cn("font-mono", resting ? "text-status-failed" : "text-foreground")}>
            {resting}
          </b>
        </span>
        <span>
          使用中 <b className="text-foreground font-mono">{busy}</b>
        </span>
        <span>
          近一小时 <Recent ok={ok} failed={failed} />
        </span>
      </div>
      {exceptions.length > 0 && (
        <p className="text-muted-foreground text-xs">
          例外：
          {exceptions
            .map((device) => `${device.instance_id} ${describe(device.rule!, false)}`)
            .join("；")}
        </p>
      )}
    </Panel>
  );
}

const resting_ = (line: Line) => line.state === "cooling" || line.state === "throttled";

/** 规则的一句话说明。 */
function useDescribe() {
  const folders = useFolders().data?.folders ?? [];
  return (rule: Rule, cluster: boolean) => {
    if (rule.source === "direct") return cluster ? "直连：用各设备自己的网络" : "直连";
    if (rule.source === "local_port") return `本机端口 ${rule.local_port}`;
    const scope = folders.find((folder) => folder.id === rule.folder_id)?.name ?? "整个代理池";
    return cluster
      ? `走代理池（${scope}）· ${pickModes[rule.mode]} · 每条线路同时 ${rule.line_limit} 个`
      : `代理池（${scope}）`;
  };
}

/** 线路小方块：颜色是状态，数字是正在用的数量；悬停看详情，点池里的线路筛出它的代理。 */
function Lines({ lines, onPick }: { lines: Line[]; onPick: (key: string) => void }) {
  if (!lines.length) {
    return (
      <p className="text-muted-foreground text-xs">
        还没有线路：代理测试通过、带上这个用途后才会出现
      </p>
    );
  }
  return (
    <div className="flex flex-wrap gap-1">
      {lines.map((line) => {
        const [label, tone] = lineState[line.state];
        const limit = line.state === "probing" ? 1 : line.limit;
        const title =
          line.kind === "pool"
            ? line.key
            : `${line.kind === "direct" ? "直连" : `本机端口 ${line.key.split(":").at(-1)}`} · ${line.device}`;
        return (
          <Tooltip key={line.key}>
            <TooltipTrigger asChild>
              <button
                type="button"
                aria-label={`线路 ${title}`}
                onClick={() => line.kind === "pool" && onPick(line.key)}
                className={cn(
                  "grid size-6 place-items-center rounded-[4px] font-mono text-[10px] font-semibold text-white",
                  toneClass[tone].dot,
                  line.kind !== "pool" && "ring-foreground/40 ring-1 ring-inset",
                )}
              >
                {line.in_use || ""}
              </button>
            </TooltipTrigger>
            <TooltipContent className="max-w-80 space-y-0.5 text-xs">
              <div className="font-mono font-semibold">{title}</div>
              {line.exit_ip && line.kind !== "pool" && <div>出口 {line.exit_ip}</div>}
              {line.proxies.length > 0 && <div>代理：{line.proxies.join("、")}</div>}
              <div>
                {label}
                {resting_(line) && ` · ${ago(line.cooldown_until)}恢复`}
                {line.reason && ` · ${line.reason}`}
              </div>
              <div>
                使用中 {line.in_use}/{limit} · 错误分 {line.score}/3 · 近一小时正常 {line.recent_ok}
                、异常 {line.recent_failed}
              </div>
            </TooltipContent>
          </Tooltip>
        );
      })}
    </div>
  );
}

function UseSettings({ use }: { use: UseCard }) {
  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button size="xs" variant="outline">
          <Settings2 /> 设置
        </Button>
      </SheetTrigger>
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>
            {use.service_name} · {use.name}
          </SheetTitle>
          <SheetDescription>
            哪些代理能用于“{use.name}”，在代理池里给代理打用途标签决定；这里设置怎么走和用多少。
          </SheetDescription>
        </SheetHeader>
        <div className="space-y-8 px-4 pb-8">
          <ClusterForm key={use.rule.updated_at} use={use} />
          <section className="space-y-3">
            <div>
              <h3 className="text-sm font-semibold">设备例外</h3>
              <p className="text-muted-foreground text-xs">
                没设例外的设备跟随上面的设置；单台设备可以直连、用它本机的代理端口，或只用某个文件夹里的代理。
              </p>
            </div>
            {use.devices.map((device) => (
              <DeviceException
                key={`${device.instance_id}-${device.rule?.updated_at}`}
                use={use.key}
                device={device}
              />
            ))}
            {!use.devices.length && (
              <p className="text-muted-foreground text-sm">还没有{use.service_name}注册</p>
            )}
          </section>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function ClusterForm({ use }: { use: UseCard }) {
  const [source, setSource] = useState(use.rule.source);
  const [folder, setFolder] = useState(use.rule.folder_id);
  const [mode, setMode] = useState(use.rule.mode);
  const [limit, setLimit] = useState(String(use.rule.line_limit));
  const done = useDone();
  const save = $api.useMutation("put", "/api/proxy-uses/{use}/rule", {
    onSuccess: () => done(`已保存“${use.name}”的设置，之后的申请按新设置分配`),
  });
  const pool = source === "pool";
  return (
    <section className="space-y-4">
      <Option label="怎么走">
        <Segmented
          value={source}
          options={[
            ["direct", "直连"],
            ["pool", "代理池"],
          ]}
          onChange={setSource}
        />
      </Option>
      <p className="text-muted-foreground text-xs">
        {pool
          ? `用代理池里带“${use.name}”用途的代理，不回落直连；出口相同的代理算一条线路。`
          : "不走代理，用各设备自己的网络出口。"}
      </p>
      {pool && (
        <>
          <Field label="范围">
            <FolderSelect value={folder} onChange={setFolder} none="整个代理池" label="范围" />
          </Field>
          <Option label="先用哪条">
            <Segmented
              value={mode}
              options={Object.entries(pickModes) as [Rule["mode"], string][]}
              onChange={setMode}
            />
          </Option>
          <p className="text-muted-foreground text-xs">
            {mode === "priority"
              ? "按文件夹的先后顺序：前面文件夹的线路满了或在冷却，才用后面的。拖动左侧的文件夹调整顺序。"
              : "所有线路轮流用，整个集群共用一个轮询位置。"}
          </p>
        </>
      )}
      <Field label="每条线路同时使用" hint="整个集群合计；同一出口同时用得太多容易被限流">
        <Input
          type="number"
          min={1}
          max={20}
          className="w-28 font-mono"
          value={limit}
          onChange={(event) => setLimit(event.target.value)}
        />
      </Field>
      <Button
        disabled={save.isPending}
        onClick={() =>
          save.mutate({
            params: { path: { use: use.key } },
            body: { source, folder_id: pool ? folder : null, mode, line_limit: Number(limit) },
          })
        }
      >
        保存
      </Button>
    </section>
  );
}

function Option({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center gap-4">
      <span className="w-16 text-sm font-medium">{label}</span>
      {children}
    </div>
  );
}

function DeviceException({ use, device }: { use: Schemas["Use"]; device: Device }) {
  const [choice, setChoice] = useState<Choice>(device.rule?.source ?? "follow");
  const [folder, setFolder] = useState(device.rule?.folder_id ?? null);
  const [port, setPort] = useState(String(device.rule?.local_port ?? ""));
  const done = useDone();
  const queryClient = useQueryClient();
  const path = { params: { path: { use, instance_id: device.instance_id } } };
  const save = $api.useMutation("put", "/api/proxy-uses/{use}/devices/{instance_id}", {
    onSuccess: () => done(`已保存 ${device.instance_id} 的例外`),
  });
  const clear = $api.useMutation("delete", "/api/proxy-uses/{use}/devices/{instance_id}", {
    onSuccess: () => done(`${device.instance_id} 已改为跟随集群`),
  });
  const test = $api.useMutation("post", "/api/proxy-uses/{use}/devices/{instance_id}/test", {
    onSuccess: (line) => {
      if (line.exit_ip) return done(`端口可用，出口 ${line.exit_ip}`);
      toast.error(`端口测试没有通过：${line.reason ?? "没有拿到出口"}`);
      void queryClient.invalidateQueries();
    },
  });
  const submit = () =>
    choice === "follow"
      ? clear.mutate(path)
      : save.mutate({
          ...path,
          body: {
            source: choice,
            folder_id: choice === "pool" ? folder : null,
            local_port: choice === "local_port" ? Number(port) || null : null,
          },
        });
  const line = device.line;
  return (
    <div className="space-y-2 rounded-md border p-3">
      <div className="flex items-center gap-3">
        <span className="font-mono text-xs">{device.instance_id}</span>
        <Status
          label={device.online ? "在线" : device.last_seen_at ? "离线" : "未注册"}
          tone={device.online ? "done" : "muted"}
        />
        <span className="text-muted-foreground ml-auto text-xs">正在用 {device.in_use}</span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Select value={choice} onValueChange={(value) => setChoice(value as Choice)}>
          <SelectTrigger size="sm" className="w-28" aria-label={`${device.instance_id} 怎么走`}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="follow">跟随集群</SelectItem>
            {Object.entries(ruleSources).map(([key, label]) => (
              <SelectItem key={key} value={key}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {choice === "pool" && (
          <div className="w-48">
            <FolderSelect value={folder} onChange={setFolder} none="整个代理池" label="范围" />
          </div>
        )}
        {choice === "local_port" && (
          <Input
            type="number"
            min={1}
            max={65535}
            placeholder="端口，如 7890"
            aria-label="本机端口"
            className="h-8 w-32 font-mono"
            value={port}
            onChange={(event) => setPort(event.target.value)}
          />
        )}
        <Button size="sm" variant="outline" onClick={submit}>
          保存
        </Button>
        {device.rule?.source === "local_port" && (
          <Button
            size="sm"
            variant="ghost"
            disabled={test.isPending}
            onClick={() => test.mutate(path)}
          >
            <FlaskConical /> {test.isPending ? "测试中…" : "测试端口"}
          </Button>
        )}
      </div>
      {line && (
        <p className="text-muted-foreground text-xs">
          自己的线路：{lineState[line.state][0]}
          {line.kind === "port" && (line.exit_ip ? ` · 出口 ${line.exit_ip}` : " · 出口未测试")}
          {resting_(line) && ` · ${ago(line.cooldown_until)}恢复`}
          {line.reason && ` · ${line.reason}`}
        </p>
      )}
    </div>
  );
}
