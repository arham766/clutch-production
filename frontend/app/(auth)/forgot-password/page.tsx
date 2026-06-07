"use client";

import { useState } from "react";
import { sendPasswordResetEmail } from "firebase/auth";
import { auth } from "../../lib/firebase";
import {
  Card,
  Field,
  FootLink,
  PageSubtitle,
  PageTitle,
  SubmitButton,
} from "../_components/AuthForm";

export default function ForgotPassword() {
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    const formData = new FormData(e.currentTarget);
    const email = formData.get("email") as string;

    try {
      await sendPasswordResetEmail(auth, email);
      setSuccess(true);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <PageTitle>Reset password</PageTitle>
      <PageSubtitle>We&apos;ll email you a link to reset it.</PageSubtitle>
      <Card>
        {success ? (
          <div className="flex flex-col items-center gap-4 py-4 text-center">
            <p className="text-sm font-semibold" style={{ color: "#32485D" }}>
              Check your email for a reset link.
            </p>
            <FootLink prompt="" href="/signin" cta="Back to Sign in" />
          </div>
        ) : (
          <form className="flex flex-col gap-3" onSubmit={handleSubmit}>
            {error && <p className="text-sm font-semibold text-red-500">{error}</p>}
            <Field name="email" label="Email" type="email" placeholder="you@company.com" required />
            <SubmitButton disabled={loading}>{loading ? "Sending..." : "Send reset link"}</SubmitButton>
            <FootLink prompt="Remembered it?" href="/signin" cta="Sign in" />
          </form>
        )}
      </Card>
    </>
  );
}
