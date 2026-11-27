"use client";

import { Maximize2, Minus, Plus } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import styles from "./builder.module.css";

const MIN = 0.4;
const MAX = 1.6;
const STEP = 0.1;
const STORAGE_KEY = "transpire.workflow-zoom";
const clamp = (value: number) => Math.round(Math.min(MAX, Math.max(MIN, value)) * 100) / 100;

function storedZoom() {
  try {
    const value = Number(window.localStorage.getItem(STORAGE_KEY));
    return value ? clamp(value) : 1;
  } catch {
    return 1;
  }
}

/**
 * Zoom for the approval route. Buttons, Ctrl/⌘ + scroll, trackpad pinch, and keyboard
 * (+, −, 0) while the canvas has focus. Long routes can be fitted to the screen.
 */
export function ZoomFrame({ className, children }: { className?: string; children: ReactNode }) {
  const frame = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const [zoom, setZoomState] = useState(1);

  useEffect(() => {
    const timer = window.setTimeout(() => setZoomState(storedZoom()), 0);
    return () => window.clearTimeout(timer);
  }, []);

  const setZoom = useCallback((next: number | ((current: number) => number)) => {
    setZoomState((current) => {
      const value = clamp(typeof next === "function" ? next(current) : next);
      try { window.localStorage.setItem(STORAGE_KEY, String(value)); } catch { /* private mode */ }
      return value;
    });
  }, []);

  const fit = () => {
    const box = frame.current;
    const inner = content.current;
    if (!box || !inner) return;
    const available = window.innerHeight - box.getBoundingClientRect().top - 48;
    const natural = inner.scrollHeight;
    setZoom(Math.min(1, available / Math.max(natural, 1)));
  };

  useEffect(() => {
    const box = frame.current;
    if (!box) return;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      setZoom((current) => current * (event.deltaY < 0 ? 1.08 : 1 / 1.08));
    };
    box.addEventListener("wheel", onWheel, { passive: false });
    return () => box.removeEventListener("wheel", onWheel);
  }, [setZoom]);

  const onKeyDown = (event: React.KeyboardEvent) => {
    if ((event.target as HTMLElement).closest("input, textarea, select")) return;
    if (event.key === "+" || event.key === "=") { event.preventDefault(); setZoom((current) => current + STEP); }
    else if (event.key === "-" || event.key === "_") { event.preventDefault(); setZoom((current) => current - STEP); }
    else if (event.key === "0") { event.preventDefault(); setZoom(1); }
  };

  return (
    <div ref={frame} className={`${className ?? ""} ${styles.zoomFrame}`} onKeyDown={onKeyDown}>
      <div className={styles.zoomBar}><div className={styles.zoomControls} role="toolbar" aria-label="Zoom">
        <button type="button" onClick={() => setZoom((current) => current - STEP)} disabled={zoom <= MIN} aria-label="Zoom out" title="Zoom out (−)"><Minus size={15} aria-hidden="true" /></button>
        <button type="button" className={styles.zoomValue} onClick={() => setZoom(1)} aria-label={`Zoom ${Math.round(zoom * 100)} percent. Reset to 100 percent`} title="Reset to 100% (0)">{Math.round(zoom * 100)}%</button>
        <button type="button" onClick={() => setZoom((current) => current + STEP)} disabled={zoom >= MAX} aria-label="Zoom in" title="Zoom in (+)"><Plus size={15} aria-hidden="true" /></button>
        <button type="button" onClick={fit} aria-label="Fit the route to the screen" title="Fit to screen"><Maximize2 size={14} aria-hidden="true" /></button>
      </div></div>
      <div ref={content} className={styles.zoomContent} style={{ zoom }}>{children}</div>
    </div>
  );
}
