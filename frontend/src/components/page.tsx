import { AlertTriangle, Inbox } from "lucide-react";
import { type ReactNode, useEffect, useReducer } from "react";
import { errorText } from "@/api/client";
import { Skeleton } from "@/components/ui/skeleton";
import { duration } from "@/lib/format";
import { cn } from "@/lib/utils";

/** 页面标题区：上方小字说明所在位置，右侧放操作。 */
export function PageHeader({
  eyebrow,
  title,
  meta,
  actions,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="rise border-foreground/10 flex flex-wrap items-end justify-between gap-4 border-b pb-5">
      <div className="min-w-0 space-y-1.5">
        {eyebrow && (
          <div className="text-muted-foreground font-mono text-[11px] tracking-[0.18em] uppercase">
            {eyebrow}
          </div>
        )}
        <h1 className="text-2xl font-semibold tracking-tight [font-stretch:110%]">{title}</h1>
        {meta && (
          <div className="text-muted-foreground flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
            {meta}
          </div>
        )}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

/** 内容分区：小标题加卡片。 */
export function Section({
  title,
  extra,
  children,
  className,
}: {
  title: ReactNode;
  extra?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("rise space-y-3", className)}>
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold tracking-wide">{title}</h2>
        {extra}
      </div>
      {children}
    </section>
  );
}

export function Panel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "bg-card rounded-lg border shadow-[0_1px_0_oklch(0.2_0.01_260/0.04)]",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** 数字指标。 */
export function Metric({ label, value, tone }: { label: string; value: ReactNode; tone?: string }) {
  return (
    <div className="space-y-1">
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className={cn("font-mono text-2xl font-semibold tabular-nums", tone)}>{value}</div>
    </div>
  );
}

/** 查询的加载、出错和空状态；有数据时渲染内容。 */
export function QueryState<T>({
  query,
  empty,
  isEmpty,
  rows = 3,
  children,
}: {
  query: { data: T | undefined; error: unknown; isPending: boolean };
  empty?: string;
  isEmpty?: (data: T) => boolean;
  rows?: number;
  children: (data: T) => ReactNode;
}) {
  if (query.isPending) {
    return (
      <div className="space-y-2 p-4">
        {Array.from({ length: rows }, (_, index) => (
          <Skeleton key={index} className="h-8 w-full" />
        ))}
      </div>
    );
  }
  if (query.error || query.data === undefined) {
    return (
      <Notice icon={<AlertTriangle className="text-status-failed size-4" />}>
        {errorText(query.error)}
      </Notice>
    );
  }
  if (isEmpty?.(query.data)) {
    return <Notice icon={<Inbox className="size-4" />}>{empty ?? "暂无数据"}</Notice>;
  }
  return children(query.data);
}

export function Notice({ icon, children }: { icon?: ReactNode; children: ReactNode }) {
  return (
    <div className="text-muted-foreground flex items-center justify-center gap-2 px-4 py-10 text-sm">
      {icon}
      {children}
    </div>
  );
}

/** 标签列表。 */
export function Tags({ tags, max }: { tags: string[] | null | undefined; max?: number }) {
  if (!tags?.length) return <span className="text-muted-foreground">—</span>;
  const shown = max ? tags.slice(0, max) : tags;
  return (
    <span className="flex flex-wrap gap-1">
      {shown.map((tag) => (
        <span key={tag} className="bg-secondary rounded-sm px-1.5 py-0.5 text-xs">
          {tag}
        </span>
      ))}
      {max && tags.length > max && (
        <span className="text-muted-foreground text-xs">+{tags.length - max}</span>
      )}
    </span>
  );
}

/** 键值信息列表。 */
export function Facts({ items }: { items: [label: string, value: ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-6 gap-y-2 text-sm">
      {items.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="min-w-0 break-words">{value ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

/** 已用时长；还没结束时每秒刷新，不依赖数据变化。 */
export function Elapsed({ start, end }: { start: string | null; end?: string | null }) {
  const [, tick] = useReducer((count: number) => count + 1, 0);
  useEffect(() => {
    if (!start || end) return;
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [start, end]);
  return duration(start, end);
}
