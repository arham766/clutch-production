import Image from "next/image";
import { LiveScene } from "./LiveScene";

export default function Home() {
  return (
    <section className="relative h-dvh w-full bg-white px-[2vmin] pt-[2vmin] pb-24">
      <div
        className="relative h-full w-full overflow-hidden rounded-[40px]"
        style={{
          backgroundImage: [
            "radial-gradient(ellipse 100% 100% at 50% 50%, rgba(202, 223, 235, 0) 50%, rgba(202, 223, 235, 0.49) 100%)",
            "linear-gradient(to bottom, #5E8EBE 0%, #77A2CA 20%, #91BDDB 60%, #CCE0EB 100%)",
          ].join(", "),
        }}
      >
        <div className="absolute inset-0 flex flex-col items-center px-8 pt-12 pb-12 text-center text-white">
          <Image
            src="/clutch-logo.svg"
            alt="Clutch"
            width={46}
            height={39}
            priority
            className="translate-x-1.5"
            style={{ filter: "brightness(0) invert(1)" }}
          />
          <LiveScene />
        </div>
      </div>

      {/* Bottom white strip: built with love using ... */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex h-24 flex-col items-center justify-center gap-2">
        <p
          className="text-sm font-semibold"
          style={{ color: "#7A8BA0", letterSpacing: "-0.02em" }}
        >
          Built with love using
        </p>
        <div className="flex items-center justify-center gap-6 opacity-75">
          {[
            ["aws.png", "AWS"],
            ["livekit.png", "LiveKit"],
            ["minimax.png", "MiniMax"],
            ["moss.png", "Moss"],
            ["qwen.png", "Qwen"],
            ["truefoundry.png", "TrueFoundry"],
            ["unsiloed.png", "Unsiloed"],
          ].map(([src, alt]) =>
            src === "qwen.png" || src === "unsiloed.png" ? (
              <span
                key={src}
                role="img"
                aria-label={alt}
                className="block h-7 w-7"
                style={{
                  backgroundColor: "#6291C0",
                  WebkitMaskImage: `url(/logos/${src})`,
                  maskImage: `url(/logos/${src})`,
                  WebkitMaskSize: "contain",
                  maskSize: "contain",
                  WebkitMaskRepeat: "no-repeat",
                  maskRepeat: "no-repeat",
                  WebkitMaskPosition: "center",
                  maskPosition: "center",
                }}
              />
            ) : (
              <img
                key={src}
                src={`/logos/${src}`}
                alt={alt}
                className="h-7 w-auto object-contain"
              />
            )
          )}
        </div>
      </div>
    </section>
  );
}
