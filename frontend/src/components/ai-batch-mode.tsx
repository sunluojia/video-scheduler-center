import { Switch } from "radix-ui";
import { useId } from "react";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";

export function AiBatchBadge() {
  return <Badge variant="outline">AI 批量</Badge>;
}

export function AiBatchSwitch({
  label,
  checked,
  onCheckedChange,
  disabled,
  creation,
}: {
  label: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
  creation?: boolean;
}) {
  const id = useId();
  const hint = `${id}-hint`;
  return (
    <div className="bg-muted/50 flex max-w-md items-start justify-between gap-3 rounded-md border px-3 py-2.5">
      <div className="space-y-1">
        <Label htmlFor={id}>{label}</Label>
        <p id={hint} className="text-muted-foreground text-xs leading-relaxed">
          画面识别和作者汇总按实时价格的 5 折异步处理。听音和超出批量大小限制的请求走实时推理。
          {creation ? "可在批次详情切换。" : "切换适用于尚未领取的任务。"}
        </p>
      </div>
      <Switch.Root
        id={id}
        aria-describedby={hint}
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
        className="bg-input focus-visible:ring-ring/50 data-[state=checked]:bg-primary inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full p-0.5 transition-colors outline-none focus-visible:ring-[3px] disabled:cursor-wait disabled:opacity-50"
      >
        <Switch.Thumb className="bg-background pointer-events-none block size-4 rounded-full shadow-xs transition-transform data-[state=checked]:translate-x-4" />
      </Switch.Root>
    </div>
  );
}
