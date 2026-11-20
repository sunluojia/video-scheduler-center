import * as React from "react";
import { cn } from "@/lib/utils";
import { Progress as ProgressPrimitive } from "radix-ui";

function Progress({
  className,
  max = 100,
  value,
  ...props
}: React.ComponentProps<typeof ProgressPrimitive.Root>) {
  const percent = value == null ? null : (value / max) * 100;
  return (
    <ProgressPrimitive.Root
      data-slot="progress"
      className={cn("bg-primary/20 relative h-2 w-full overflow-hidden rounded-full", className)}
      max={max}
      value={value}
      {...props}
    >
      <ProgressPrimitive.Indicator
        data-slot="progress-indicator"
        className={cn(
          "h-full w-full",
          percent === null
            ? "bg-primary/40 motion-safe:animate-pulse"
            : "bg-primary transition-transform",
        )}
        style={percent === null ? undefined : { transform: `translateX(${percent - 100}%)` }}
      />
    </ProgressPrimitive.Root>
  );
}

export { Progress };
