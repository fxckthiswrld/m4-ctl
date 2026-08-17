import * as React from "react";
import { cn } from "@/lib/utils";

export interface ToggleProps {
  checked: boolean;
  disabled?: boolean;
  className?: string;
  onChange?: (checked: boolean) => void;
}

export function Toggle({ checked, disabled, className, onChange }: ToggleProps) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange?.(!checked)}
      className={cn(
        "relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50",
        checked ? "bg-primary" : "bg-secondary",
        className
      )}
    >
      <span
        className={cn(
          "pointer-events-none block h-5 w-5 rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-6" : "translate-x-1"
        )}
      />
    </button>
  );
}