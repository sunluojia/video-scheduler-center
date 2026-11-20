import type { Schemas } from "@/api/client";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ago } from "@/lib/format";
import { lineState, toneClass } from "@/lib/status";
import { cn } from "@/lib/utils";
import { depths, type Tag, UNFILED, useFolders, useTagName, useUses } from "./data";

/** 最近一小时归还时报告正常和异常的次数。 */
export function Recent({ ok, failed }: { ok: number; failed: number }) {
  return (
    <span className="font-mono text-xs" title="最近一小时：正常 / 异常">
      <span className="text-status-done">{ok}</span>
      <span className="text-muted-foreground"> / </span>
      <span className={failed ? "text-status-failed" : "text-muted-foreground"}>{failed}</span>
    </span>
  );
}

/** 分段选择。 */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: [T, string][];
  onChange: (value: T) => void;
}) {
  return (
    <div className="bg-muted inline-flex rounded-md p-0.5">
      {options.map(([key, label]) => (
        <button
          key={key}
          type="button"
          aria-pressed={value === key}
          onClick={() => onChange(key)}
          className={cn(
            "rounded-[5px] px-3 py-1 text-sm transition-colors",
            value === key
              ? "bg-card text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

/**
 * 选用途：每个服务一行，“全部任务”表示这个服务的任务都能用（以后新增的也算），
 * 也可以只选其中几类任务。
 */
export function UsePicker({ value, onChange }: { value: Tag[]; onChange: (value: Tag[]) => void }) {
  const uses = useUses().data ?? [];
  const services = [...new Map(uses.map((use) => [use.service, use.service_name]))];
  const toggle = (tag: Tag) =>
    onChange(value.includes(tag) ? value.filter((item) => item !== tag) : [...value, tag]);
  return (
    <div className="space-y-2">
      {services.map(([service, name]) => {
        const whole = value.includes(service);
        return (
          <div key={service} className="flex flex-wrap items-center gap-1.5">
            <span className="text-muted-foreground mr-1 w-16 text-xs">{name}</span>
            <Pill
              on={whole}
              onClick={() =>
                onChange(
                  whole
                    ? value.filter((tag) => tag !== service)
                    : [...value.filter((tag) => !tag.startsWith(`${service}.`)), service],
                )
              }
            >
              全部任务
            </Pill>
            {uses
              .filter((use) => use.service === service)
              .map((use) => (
                <Pill
                  key={use.key}
                  on={whole || value.includes(use.key)}
                  disabled={whole}
                  onClick={() => toggle(use.key)}
                >
                  {use.name}
                </Pill>
              ))}
          </div>
        );
      })}
    </div>
  );
}

function Pill({
  on,
  disabled,
  onClick,
  children,
}: {
  on: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "rounded-full border px-2.5 py-0.5 text-xs transition-colors disabled:cursor-default",
        on
          ? "border-signal bg-signal/15 text-foreground"
          : "text-muted-foreground hover:border-foreground/30",
        disabled && "opacity-70",
      )}
    >
      {children}
    </button>
  );
}

/**
 * 代理表里的用途：每个能用的用途一个标签，色点是它所在线路在这个用途上的状态；
 * 代理本身还不能用（未测试、停用等）时色点是灰的。
 */
export function UseStates({ states, idle }: { states: Schemas["UseState"][]; idle: boolean }) {
  const tagName = useTagName();
  if (!states.length)
    return <span className="text-muted-foreground text-xs">未设用途，不会分配</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {states.map((item) => {
        const [label, tone] = lineState[item.state];
        const resting = item.state === "cooling" || item.state === "throttled";
        const title = [
          `${tagName(item.use)}：${idle ? "代理还不能用" : label}`,
          !idle && resting && `${ago(item.cooldown_until)}恢复`,
          !idle && item.reason,
        ];
        return (
          <span
            key={item.use}
            title={title.filter(Boolean).join("，")}
            className="bg-secondary inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-xs"
          >
            <span className={cn("size-1.5 rounded-full", toneClass[idle ? "muted" : tone].dot)} />
            {tagName(item.use)}
          </span>
        );
      })}
    </span>
  );
}

/** 选文件夹，按层级缩进；none 是“不放文件夹”或“整个代理池”。 */
export function FolderSelect({
  value,
  onChange,
  none,
  label,
}: {
  value: string | null;
  onChange: (value: string | null) => void;
  none: string;
  label: string;
}) {
  const folders = useFolders().data?.folders ?? [];
  const depth = depths(folders);
  return (
    <Select
      value={value ?? UNFILED}
      onValueChange={(next) => onChange(next === UNFILED ? null : next)}
    >
      <SelectTrigger className="w-full" aria-label={label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={UNFILED}>{none}</SelectItem>
        {folders.map((folder) => (
          <SelectItem key={folder.id} value={folder.id}>
            <span style={{ paddingLeft: `${(depth.get(folder.id) ?? 0) * 0.9}rem` }}>
              {folder.name}
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
