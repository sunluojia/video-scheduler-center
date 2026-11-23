import { cn } from "@/lib/utils";

/** 一组互斥的小按钮，用来切换图表类型、时间段等。 */
export function Choice<T extends string>({
  label,
  options,
  value,
  onChange,
  disabled = [],
}: {
  label: string;
  options: Record<T, string>;
  value: T;
  onChange: (value: T) => void;
  disabled?: T[];
}) {
  return (
    <div role="group" aria-label={label} className="bg-muted inline-flex rounded-md p-0.5">
      {(Object.keys(options) as T[]).map((key) => (
        <button
          key={key}
          type="button"
          aria-pressed={key === value}
          disabled={disabled.includes(key)}
          onClick={() => onChange(key)}
          className={cn(
            "rounded px-2.5 py-1 text-xs whitespace-nowrap transition-colors disabled:opacity-40",
            key === value
              ? "bg-card text-foreground font-medium shadow-xs"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {options[key]}
        </button>
      ))}
    </div>
  );
}
