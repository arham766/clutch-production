import Image from "next/image";

export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <section className="h-dvh w-full bg-white p-[2vmin]">
      <div
        className="relative flex h-full w-full flex-col items-center overflow-hidden rounded-[40px] px-8 pt-12 pb-12"
        style={{
          backgroundImage: [
            "radial-gradient(ellipse 100% 100% at 50% 50%, rgba(202, 223, 235, 0) 50%, rgba(202, 223, 235, 0.49) 100%)",
            "linear-gradient(to bottom, #5E8EBE 0%, #77A2CA 20%, #91BDDB 60%, #CCE0EB 100%)",
          ].join(", "),
        }}
      >
        <div className="flex flex-col items-center text-white">
          <Image
            src="/clutch-logo.svg"
            alt="Clutch"
            width={56}
            height={47}
            priority
            className="translate-x-2"
            style={{ filter: "brightness(0) invert(1)" }}
          />
          <h2
            className="mt-4 text-[2.8125rem] font-bold leading-none"
            style={{ letterSpacing: "-0.065em" }}
          >
            Clutch
          </h2>
        </div>
        <div className="my-auto flex w-full flex-col items-center">
          {children}
        </div>
      </div>
    </section>
  );
}
