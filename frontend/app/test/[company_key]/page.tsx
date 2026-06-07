import { LiveWidgetScene } from "./LiveWidgetScene";
import Image from "next/image";

export default async function TestWidgetPage(props: { params: Promise<{ company_key: string }>, searchParams: Promise<{ product_id?: string }> }) {
  const params = await props.params;
  const searchParams = await props.searchParams;
  const companyKey = params.company_key;
  const productId = searchParams.product_id || "";

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
          <h2
            className="mt-3 text-[2.25rem] font-bold leading-none"
            style={{ letterSpacing: "-0.065em" }}
          >
            Clutch Widget Test
          </h2>
          <p className="mt-2 text-sm opacity-80">Testing Product ID: {productId}</p>
          
          <LiveWidgetScene companyKey={companyKey} productId={productId} />
        </div>
      </div>
    </section>
  );
}
