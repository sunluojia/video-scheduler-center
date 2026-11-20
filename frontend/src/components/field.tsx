import type { ReactNode } from "react";

/**
 * 表单项：标签在上，下方显示错误或提示。
 * 标签包住输入框，点击标签聚焦，读屏和测试也能按标签找到输入框；
 * 内容是一组按钮时传 group，不能包在 label 里，否则点标签文字会按下第一个按钮。
 */
export function Field({
  label,
  hint,
  error,
  group,
  children,
}: {
  label: string;
  hint?: ReactNode;
  error?: string;
  group?: boolean;
  children: ReactNode;
}) {
  const Box = group ? "div" : "label";
  return (
    <div className="space-y-1.5">
      <Box
        className="grid gap-1.5"
        role={group ? "group" : undefined}
        aria-label={group ? label : undefined}
      >
        <span className="text-sm leading-none font-medium">{label}</span>
        {children}
      </Box>
      {error ? (
        <p className="text-status-failed text-xs">{error}</p>
      ) : (
        hint && <p className="text-muted-foreground text-xs">{hint}</p>
      )}
    </div>
  );
}
