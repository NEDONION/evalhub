import type { ButtonHTMLAttributes } from "react";

import { cn } from "../../lib/utils";

type ButtonVariant = "primary" | "secondary" | "ghost";
type ButtonSize = "default" | "sm";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export function Button({
  className,
  variant = "primary",
  size = "default",
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md border text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-55",
        size === "default" ? "h-10 px-4" : "h-8 px-3 text-xs",
        variant === "primary" &&
          "border-primary bg-primary text-white shadow-[0_1px_2px_rgba(37,99,235,0.2)] hover:border-primary-hover hover:bg-primary-hover",
        variant === "secondary" &&
          "border-border bg-surface text-ink shadow-[0_1px_2px_rgba(15,23,42,0.04)] hover:border-slate-300 hover:bg-slate-50",
        variant === "ghost" && "border-transparent bg-transparent text-muted hover:bg-slate-100 hover:text-ink",
        className,
      )}
      {...props}
    />
  );
}
