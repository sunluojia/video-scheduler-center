import { type Tone, toneClass } from "@/lib/status";
import { cn } from "@/lib/utils";

/** 状态标记：色点加文字，进行中的色点带呼吸效果。 */
export function Status({
  label,
  tone,
  className,
}: {
  label: string;
  tone: Tone;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 text-xs font-medium whitespace-nowrap",
        toneClass[tone].text,
        className,
      )}
    >
      <span className="relative flex size-2">
        {tone === "active" && (
          <span
            className={cn(
              "absolute inline-flex size-full animate-ping rounded-full opacity-50",
              toneClass[tone].dot,
            )}
          />
        )}
        <span className={cn("relative inline-flex size-2 rounded-full", toneClass[tone].dot)} />
      </span>
      {label}
    </span>
  );
}

/** 按状态表显示一个状态值。 */
export function StatusOf<T extends string>({
  labels,
  value,
}: {
  labels: Record<T, [string, Tone]>;
  value: T;
}) {
  const [label, tone] = labels[value];
  return <Status label={label} tone={tone} />;
}

type Segment = { status: string; label: string; tone: Tone; count: number };

/** 分段进度条：各状态按数量占比排列，悬停显示数量。 */
export function SegmentBar({ segments, className }: { segments: Segment[]; className?: string }) {
  const sum = segments.reduce((value, segment) => value + segment.count, 0);
  return (
    <div className={cn("bg-muted flex h-1.5 w-full overflow-hidden rounded-full", className)}>
      {segments.map((segment) => (
        <div
          key={segment.status}
          title={`${segment.label} ${segment.count}`}
          className={cn("h-full transition-[width] duration-500", toneClass[segment.tone].bar)}
          style={{ width: `${(segment.count / Math.max(sum, 1)) * 100}%` }}
        />
      ))}
    </div>
  );
}

/** 进度条的图例和数量。 */
export function Legend({ segments }: { segments: Segment[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1">
      {segments.map((segment) => (
        <span
          key={segment.status}
          className="text-muted-foreground inline-flex items-center gap-1.5 text-xs"
        >
          <span className={cn("size-2 rounded-[2px]", toneClass[segment.tone].dot)} />
          {segment.label}
          <span className="text-foreground font-mono">{segment.count}</span>
        </span>
      ))}
    </div>
  );
}
