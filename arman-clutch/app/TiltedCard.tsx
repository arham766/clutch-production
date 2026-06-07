"use client";

import { motion, useMotionValue, useSpring } from "motion/react";
import { useRef, type CSSProperties, type ReactNode } from "react";

const springValues = {
  damping: 30,
  stiffness: 100,
  mass: 2,
};

export function TiltedCard({
  children,
  className,
  innerClassName,
  style,
  innerStyle,
  rotateAmplitude = 10,
  scaleOnHover = 1.04,
  perspective = 1200,
}: {
  children: ReactNode;
  className?: string;
  innerClassName?: string;
  style?: CSSProperties;
  innerStyle?: CSSProperties;
  rotateAmplitude?: number;
  scaleOnHover?: number;
  perspective?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);

  const rotateX = useSpring(useMotionValue(0), springValues);
  const rotateY = useSpring(useMotionValue(0), springValues);
  const scale = useSpring(1, springValues);

  function handleMove(e: React.MouseEvent<HTMLDivElement>) {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const offsetX = e.clientX - rect.left - rect.width / 2;
    const offsetY = e.clientY - rect.top - rect.height / 2;
    rotateX.set((offsetY / (rect.height / 2)) * -rotateAmplitude);
    rotateY.set((offsetX / (rect.width / 2)) * rotateAmplitude);
  }

  function handleEnter() {
    scale.set(scaleOnHover);
  }

  function handleLeave() {
    scale.set(1);
    rotateX.set(0);
    rotateY.set(0);
  }

  return (
    <div
      ref={ref}
      onMouseMove={handleMove}
      onMouseEnter={handleEnter}
      onMouseLeave={handleLeave}
      className={className}
      style={{ perspective: `${perspective}px`, ...style }}
    >
      <motion.div
        className={innerClassName}
        style={{
          rotateX,
          rotateY,
          scale,
          transformStyle: "preserve-3d",
          willChange: "transform",
          ...innerStyle,
        }}
      >
        {children}
      </motion.div>
    </div>
  );
}
