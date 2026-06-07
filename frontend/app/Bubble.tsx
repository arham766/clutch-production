"use client";

import { useLayoutEffect, useRef, type CSSProperties, type ReactNode } from "react";

export function Bubble({
  children,
  className,
  style,
}: {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;

    const measure = () => {
      el.style.width = "auto";
      const textNode = el.firstChild;
      if (!textNode || textNode.nodeType !== Node.TEXT_NODE) return;
      const range = document.createRange();
      range.selectNodeContents(textNode);
      const rects = range.getClientRects();
      let maxLine = 0;
      for (const r of rects) maxLine = Math.max(maxLine, r.width);
      if (maxLine > 0) {
        const cs = getComputedStyle(el);
        const pad =
          parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
        el.style.width = `${Math.ceil(maxLine + pad)}px`;
      }
    };

    measure();
    if (document.fonts && document.fonts.status !== "loaded") {
      document.fonts.ready.then(measure);
    }

    const ro = new ResizeObserver(() => measure());
    if (el.parentElement) ro.observe(el.parentElement);
    return () => ro.disconnect();
  }, [children]);

  return (
    <div ref={ref} className={className} style={style}>
      {children}
    </div>
  );
}
