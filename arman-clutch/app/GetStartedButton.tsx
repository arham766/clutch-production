"use client";

import { useState } from "react";
import { LiveAgent } from "./LiveAgent";

// Identical markup to the original landing-page "Get started" button — only adds an onClick that
// launches the live Clutch agent overlay. Visual treatment is unchanged.
export function GetStartedButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fade-rise mt-6 rounded-xl bg-white px-5 py-2 text-sm font-semibold transition-transform duration-200 hover:scale-[1.06] active:scale-95"
        style={{
          color: "#5E8EBE",
          letterSpacing: "-0.02em",
          animationDelay: "1.6s",
        }}
      >
        Get started
      </button>
      {open && <LiveAgent onClose={() => setOpen(false)} />}
    </>
  );
}
