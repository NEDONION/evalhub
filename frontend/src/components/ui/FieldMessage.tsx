import type { HTMLAttributes } from "react";

import { cn } from "../../lib/utils";

export function FieldMessage({ className, ...props }: HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("mt-1 text-xs leading-5 text-red-600", className)} {...props} />;
}
