/**
 * Tooltip — an absolutely positioned overlay rendered at a pixel coordinate.
 *
 * Rendered via a React portal into document.body so it is never clipped
 * by an ancestor's overflow:hidden (e.g. the map container).
 * The caller is responsible for positioning: pass the clientX/clientY of
 * the pointer event from Deck.gl's onClick callback.
 */

import { type ReactNode, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

interface TooltipProps {
  x: number;
  y: number;
  children: ReactNode;
  onClose: () => void;
}

export function Tooltip({ x, y, children, onClose }: TooltipProps) {
  const ref = useRef<HTMLDivElement>(null);

  // Close on Escape key or click outside.
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener('keydown', handleKey);
    document.addEventListener('mousedown', handleClick);
    return () => {
      document.removeEventListener('keydown', handleKey);
      document.removeEventListener('mousedown', handleClick);
    };
  }, [onClose]);

  // Nudge left/up if the tooltip would overflow the viewport.
  const OFFSET = 12;
  const W = 240; // approximate width
  const adjustedX = x + OFFSET + W > window.innerWidth ? x - W - OFFSET : x + OFFSET;
  const adjustedY = y + OFFSET;

  return createPortal(
    <div
      ref={ref}
      role="tooltip"
      aria-live="polite"
      style={{
        position: 'fixed',
        top: adjustedY,
        left: adjustedX,
        zIndex: 9999,
        /* Use design-system tokens so the tooltip matches the panel chrome */
        background: 'color-mix(in srgb, var(--surface-panel) 97%, transparent)',
        backdropFilter: 'blur(10px)',
        WebkitBackdropFilter: 'blur(10px)',
        border: '1px solid var(--line-grid)',
        color: 'var(--text-primary)',
        borderRadius: 8,
        padding: '10px 14px',
        fontSize: 'var(--text-caption)',
        lineHeight: 1.6,
        pointerEvents: 'none',
        width: W,
        boxShadow: '0 6px 24px rgba(0,0,0,0.55), 0 1px 4px rgba(0,0,0,0.3)',
        fontFamily: 'var(--font-body)',
      }}
    >
      {children}
    </div>,
    document.body,
  );
}
