"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { signInWithEmailAndPassword, signInWithPopup } from "firebase/auth";
import { auth, googleProvider } from "../../lib/firebase";
import { api } from "../../lib/api";
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

export default function SignIn() {
  const router = useRouter();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    const formData = new FormData(e.currentTarget);
    const email = formData.get("email") as string;
    const password = formData.get("password") as string;

    try {
      await signInWithEmailAndPassword(auth, email, password);
      router.push("/dashboard");
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleGoogle = async () => {
    try {
      setLoading(true);
      const cred = await signInWithPopup(auth, googleProvider);
      const name = cred.user.displayName || "My";
      const token = await cred.user.getIdToken();
      await api.bootstrap(name + "'s Company", token).catch(() => {});
      router.push("/dashboard");
    } catch (err: any) {
      if (auth.currentUser) {
        const name = auth.currentUser.displayName || "My";
        const token = await auth.currentUser.getIdToken();
        await api.bootstrap(name + "'s Company", token).catch(() => {});
        router.push("/dashboard");
        return;
      }
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <PageTitle>Sign in</PageTitle>
      <Card>
        <form className="flex flex-col gap-3" onSubmit={handleSubmit}>
          <GoogleButton label="Continue with Google" onClick={handleGoogle} disabled={loading} />
          <Divider />
          {error && <p className="text-sm font-semibold text-red-500">{error}</p>}
          <Field name="email" label="Email" type="email" placeholder="you@company.com" required />
          <Field
            name="password"
            label="Password"
            type="password"
            placeholder="••••••••"
            required
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
          <SubmitButton disabled={loading}>{loading ? "Signing in..." : "Sign in"}</SubmitButton>
          <FootLink prompt="Don't have an account?" href="/signup" cta="Sign up" />
        </form>
      </Card>
    </>
  );
}
