import * as React from "react";
import { cn } from "@/lib/utils";

export interface SliderProps {
  value: number;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  className?: string;
  "aria-label": string;
  onChange?: (value: number) => void;
  onCommit?: (value: number) => void;
  onInteractionChange?: (active: boolean) => void;
}

export function Slider({
  value,
  min = 0,
  max = 100,
  step = 1,
  disabled,
  className,
  "aria-label": label,
  onChange,
  onCommit,
  onInteractionChange,
}: SliderProps) {
  const ref = React.useRef<HTMLDivElement>(null);
  const dragRef = React.useRef(false);
  const keyboardRef = React.useRef<number | null>(null);
  const interactionCallback = React.useRef(onInteractionChange);
  interactionCallback.current = onInteractionChange;

  function cancelInteraction() {
    if (!dragRef.current && keyboardRef.current === null) return;
    dragRef.current = false;
    keyboardRef.current = null;
    interactionCallback.current?.(false);
  }

  React.useEffect(() => {
    if (disabled) cancelInteraction();
  }, [disabled]);
  React.useEffect(() => () => cancelInteraction(), []);

  const clamp = (v: number) => Math.min(max, Math.max(min, v));
  const snap = (v: number) => clamp(min + Math.round((v - min) / step) * step);

  function commitKeyboard() {
    const nextValue = keyboardRef.current;
    if (nextValue === null) return;
    keyboardRef.current = null;
    onInteractionChange?.(false);
    if (!disabled) onCommit?.(nextValue);
  }

  const pct = max === min ? 0 : ((clamp(value) - min) / (max - min)) * 100;

  function valueFromEvent(e: React.PointerEvent) {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return value;
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    return snap(min + ratio * (max - min));
  }

  return (
    <div
      ref={ref}
      role="slider"
      tabIndex={disabled ? -1 : 0}
      aria-label={label}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={clamp(value)}
      aria-disabled={Boolean(disabled)}
      aria-orientation="horizontal"
      className={cn(
        // Keep a 44px interaction area around the 4px track, as recommended
        // by Apple HIG, while preserving a compact visual control.
        "relative h-11 w-full touch-none select-none rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        disabled && "pointer-events-none opacity-50",
        className
      )}
      onPointerDown={(e) => {
        if (disabled || e.button !== 0 || keyboardRef.current !== null) return;
        e.currentTarget.focus();
        dragRef.current = true;
        onInteractionChange?.(true);
        e.currentTarget.setPointerCapture(e.pointerId);
        onChange?.(valueFromEvent(e));
      }}
      onPointerMove={(e) => {
        if (!disabled && dragRef.current) onChange?.(valueFromEvent(e));
      }}
      onPointerUp={(e) => {
        if (disabled || !dragRef.current) return;
        const nextValue = valueFromEvent(e);
        dragRef.current = false;
        onInteractionChange?.(false);
        if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
        onChange?.(nextValue);
        onCommit?.(nextValue);
      }}
      onPointerCancel={cancelInteraction}
      onLostPointerCapture={cancelInteraction}
      onKeyDown={(e) => {
        if (disabled || dragRef.current || e.altKey || e.ctrlKey || e.metaKey) return;
        const current = keyboardRef.current ?? value;
        let next: number;
        switch (e.key) {
          case "ArrowRight": case "ArrowUp": next = snap(current + step); break;
          case "ArrowLeft": case "ArrowDown": next = snap(current - step); break;
          case "Home": next = min; break;
          case "End": next = max; break;
          default: return;
        }
        e.preventDefault();
        if (next === current) return;
        if (keyboardRef.current === null) onInteractionChange?.(true);
        keyboardRef.current = next;
        onChange?.(next);
      }}
      onKeyUp={(e) => {
        if (["ArrowRight", "ArrowUp", "ArrowLeft", "ArrowDown", "Home", "End"].includes(e.key)) commitKeyboard();
      }}
      onBlur={commitKeyboard}
    >
      <div className="absolute top-1/2 h-1 w-full -translate-y-1/2 rounded-full bg-secondary" />
      <div
        className="absolute top-1/2 h-1 -translate-y-1/2 rounded-full bg-primary"
        style={{ width: `${pct}%` }}
      />
      <div
        aria-hidden="true"
        className="absolute top-1/2 h-7 w-7 -translate-x-1/2 -translate-y-1/2 rounded-full border border-primary/30 bg-white shadow-md shadow-black/25 transition-[box-shadow,transform] duration-100"
        style={{ left: `${pct}%` }}
      />
    </div>
  );
}
