import type { Metadata } from "next";
import {
  Card,
  Field,
  FootLink,
  PageSubtitle,
  PageTitle,
  SubmitButton,
} from "../_components/AuthForm";

export const metadata: Metadata = { title: "Reset Password" };

export default function ForgotPassword() {
  return (
    <>
      <PageTitle>Reset password</PageTitle>
      <PageSubtitle>We&apos;ll email you a link to reset it.</PageSubtitle>
      <Card>
        <form className="flex flex-col gap-3">
          <Field label="Email" type="email" placeholder="you@company.com" />
          <SubmitButton>Send reset link</SubmitButton>
          <FootLink prompt="Remembered it?" href="/signin" cta="Sign in" />
        </form>
      </Card>
    </>
  );
}
