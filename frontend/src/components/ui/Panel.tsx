import type { HTMLAttributes } from "react";

import { cn } from "../../lib/utils";

export function Panel({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return (
    <section
      className={cn(
        "rounded-lg border border-border bg-surface shadow-[0_1px_2px_rgba(15,23,42,0.035)]",
        className,
      )}
      {...props}
    />
  );
}
