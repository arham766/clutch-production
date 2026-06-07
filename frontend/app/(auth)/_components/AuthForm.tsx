import Link from "next/link";
import type { CSSProperties, ReactNode } from "react";

const inputBorder: CSSProperties = {
  border: "1.5px solid #E6E8EB",
  color: "#32485D",
  letterSpacing: "-0.02em",
};

export function PageTitle({ children }: { children: ReactNode }) {
  return (
    <h1
      className="text-center text-[3.5rem] font-semibold leading-[1.05] text-white"
      style={{ letterSpacing: "-0.065em" }}
    >
      {children}
    </h1>
  );
}

export function PageSubtitle({ children }: { children: ReactNode }) {
  return (
    <p
      className="mt-4 max-w-md text-center text-lg font-semibold leading-[1.2] text-white/90"
      style={{ letterSpacing: "-0.035em" }}
    >
      {children}
    </p>
  );
}

export function Card({ children }: { children: ReactNode }) {
  return (
    <div className="mt-8 w-full max-w-md rounded-3xl bg-white p-8">
      {children}
    </div>
  );
}

export function GoogleButton({ label, onClick, disabled }: { label: string; onClick?: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex h-11 w-full items-center justify-center gap-2.5 rounded-xl text-sm font-semibold disabled:opacity-50"
      style={inputBorder}
    >
      <svg
        width="18"
        height="18"
        viewBox="0 0 18 18"
        xmlns="http://www.w3.org/2000/svg"
        fill="#6291C0"
      >
        <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.259h2.908c1.702-1.567 2.684-3.875 2.684-6.615z" />
        <path d="M9 18c2.43 0 4.467-.806 5.956-2.184l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z" />
        <path d="M3.964 10.706A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.706V4.962H.957A8.997 8.997 0 0 0 0 9c0 1.452.348 2.827.957 4.038l3.007-2.332z" />
        <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.962L3.964 7.294C4.672 5.167 6.656 3.58 9 3.58z" />
      </svg>
      {label}
    </button>
  );
}

export function Divider() {
  return (
    <div className="my-1 flex items-center gap-3">
      <div className="h-px flex-1" style={{ backgroundColor: "#E6E8EB" }} />
      <span
        className="text-xs font-medium"
        style={{ color: "#7A8BA0", letterSpacing: "-0.02em" }}
      >
        or
      </span>
      <div className="h-px flex-1" style={{ backgroundColor: "#E6E8EB" }} />
    </div>
  );
}

export function Field({
  label,
  rightSlot,
  ...inputProps
}: {
  label: string;
  rightSlot?: ReactNode;
} & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <label
          className="text-xs font-semibold"
          style={{ color: "#32485D", letterSpacing: "-0.02em" }}
        >
          {label}
        </label>
        {rightSlot}
      </div>
      <input
        className="h-11 w-full rounded-xl bg-white px-3.5 text-sm font-medium outline-none placeholder:font-medium disabled:opacity-50"
        style={inputBorder}
        {...inputProps}
      />
    </div>
  );
}

export function SubmitButton({ children, disabled }: { children: ReactNode; disabled?: boolean }) {
  return (
    <button
      type="submit"
      disabled={disabled}
      className="h-11 w-full rounded-xl text-sm font-semibold text-white disabled:opacity-50"
      style={{ backgroundColor: "#6291C0", letterSpacing: "-0.02em" }}
    >
      {children}
    </button>
  );
}

export function FootLink({
  prompt,
  href,
  cta,
}: {
  prompt: string;
  href: string;
  cta: string;
}) {
  return (
    <p
      className="mt-5 text-center text-sm font-medium"
      style={{ color: "#7A8BA0", letterSpacing: "-0.02em" }}
    >
      {prompt}{" "}
      <Link href={href} className="font-semibold" style={{ color: "#6291C0" }}>
        {cta}
      </Link>
    </p>
  );
}
