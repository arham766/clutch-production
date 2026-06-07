import type { Metadata } from "next";
import Link from "next/link";
import {
  Card,
  Divider,
  Field,
  FootLink,
  GoogleButton,
  PageTitle,
  SubmitButton,
} from "../_components/AuthForm";

export const metadata: Metadata = { title: "Sign In" };

export default function SignIn() {
  return (
    <>
      <PageTitle>Sign in</PageTitle>
      <Card>
        <form className="flex flex-col gap-3">
          <GoogleButton label="Continue with Google" />
          <Divider />
          <Field label="Email" type="email" placeholder="you@company.com" />
          <Field
            label="Password"
            type="password"
            placeholder="••••••••"
            rightSlot={
              <Link
                href="/forgot-password"
                className="text-xs font-semibold"
                style={{ color: "#6291C0", letterSpacing: "-0.02em" }}
              >
                Forgot?
              </Link>
            }
          />
          <SubmitButton>Sign in</SubmitButton>
          <FootLink prompt="Don't have an account?" href="/signup" cta="Sign up" />
        </form>
      </Card>
    </>
  );
}
