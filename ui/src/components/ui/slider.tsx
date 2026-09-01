import * as React from "react";
import { cn } from "@/lib/utils";

export interface SliderProps {
  value: number;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  className?: string;
  onChange?: (value: number) => void;
  onCommit?: (value: number) => void;
}

export function Slider({
  value,
  min = 0,
  max = 100,
  step = 1,
  disabled,
  className,
  onChange,
  onCommit,
}: SliderProps) {
  const ref = React.useRef<HTMLDivElement>(null);
  const dragRef = React.useRef(false);

  const pct = max === min ? 0 : ((value - min) / (max - min)) * 100;

  function valueFromEvent(e: React.PointerEvent) {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return value;
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    let v = min + ratio * (max - min);
    v = Math.round(v / step) * step;
    return Math.min(max, Math.max(min, v));
  }

  return (
    <div
      ref={ref}
      className={cn(
        "relative h-6 w-full touch-none select-none",
        disabled && "pointer-events-none opacity-50",
        className
      )}
      onPointerDown={(e) => {
        dragRef.current = true;
        e.currentTarget.setPointerCapture(e.pointerId);
        onChange?.(valueFromEvent(e));
      }}
      onPointerMove={(e) => {
        if (dragRef.current) onChange?.(valueFromEvent(e));
      }}
      onPointerUp={(e) => {
        if (!dragRef.current) return;
        const nextValue = valueFromEvent(e);
        dragRef.current = false;
        e.currentTarget.releasePointerCapture(e.pointerId);
        onChange?.(nextValue);
        onCommit?.(nextValue);
      }}
      onPointerCancel={() => {
        dragRef.current = false;
      }}
    >
      <div className="absolute top-1/2 h-1.5 w-full -translate-y-1/2 rounded-full bg-secondary" />
      <div
        className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-primary"
        style={{ width: `${pct}%` }}
      />
      <div
        className="absolute top-1/2 h-5 w-5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-primary bg-white shadow"
        style={{ left: `${pct}%` }}
      />
    </div>
  );
}
