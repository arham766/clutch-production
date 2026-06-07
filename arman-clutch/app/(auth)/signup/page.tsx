import type { Metadata } from "next";
import {
  Card,
  Divider,
  Field,
  FootLink,
  GoogleButton,
  PageTitle,
  SubmitButton,
} from "../_components/AuthForm";

export const metadata: Metadata = { title: "Sign Up" };

export default function SignUp() {
  return (
    <>
      <PageTitle>Create account</PageTitle>
      <Card>
        <form className="flex flex-col gap-3">
          <GoogleButton label="Sign up with Google" />
          <Divider />
          <Field label="Name" type="text" placeholder="Jane Doe" />
          <Field label="Email" type="email" placeholder="you@company.com" />
          <Field label="Password" type="password" placeholder="••••••••" />
          <SubmitButton>Create account</SubmitButton>
          <FootLink prompt="Already have an account?" href="/signin" cta="Sign in" />
        </form>
      </Card>
    </>
  );
}
