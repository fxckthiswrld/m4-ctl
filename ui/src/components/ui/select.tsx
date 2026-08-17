import * as React from "react";
import { cn } from "@/lib/utils";

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectProps {
  value: string;
  options: SelectOption[];
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
  title?: string;
  onChange?: (value: string) => void;
}

export function Select({
  value,
  options,
  disabled,
  className,
  "aria-label": ariaLabel,
  title,
  onChange,
}: SelectProps) {
  return (
    <select
      value={value}
      disabled={disabled}
      aria-label={ariaLabel}
      title={title}
      onChange={(e) => onChange?.(e.target.value)}
      className={cn(
        "h-9 min-w-0 max-w-full rounded-md border border-border bg-secondary px-3 text-sm text-foreground",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
        disabled && "opacity-50",
        className
      )}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value} className="bg-background">
          {o.label}
        </option>
      ))}
    </select>
  );
}
