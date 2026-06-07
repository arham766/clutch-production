"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createUserWithEmailAndPassword, updateProfile, signInWithPopup } from "firebase/auth";
import { auth, googleProvider } from "../../lib/firebase";
import { api } from "../../lib/api";
import {
  Card,
  Divider,
  Field,
  FootLink,
  GoogleButton,
  PageTitle,
  SubmitButton,
} from "../_components/AuthForm";

export default function SignUp() {
  const router = useRouter();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    const formData = new FormData(e.currentTarget);
    const name = formData.get("name") as string;
    const email = formData.get("email") as string;
    const password = formData.get("password") as string;

    try {
      const cred = await createUserWithEmailAndPassword(auth, email, password);
      await updateProfile(cred.user, { displayName: name });
      
      // Bootstrap the company on the backend
      await api.bootstrap(name + "'s Company");
      
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
      await api.bootstrap(name + "'s Company", token);
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
      <PageTitle>Create account</PageTitle>
      <Card>
        <form className="flex flex-col gap-3" onSubmit={handleSubmit}>
          <GoogleButton label="Sign up with Google" onClick={handleGoogle} disabled={loading} />
          <Divider />
          {error && <p className="text-sm font-semibold text-red-500">{error}</p>}
          <Field name="name" label="Name" type="text" placeholder="Jane Doe" required />
          <Field name="email" label="Email" type="email" placeholder="you@company.com" required />
          <Field name="password" label="Password" type="password" placeholder="••••••••" required minLength={6} />
          <SubmitButton disabled={loading}>{loading ? "Creating..." : "Create account"}</SubmitButton>
          <FootLink prompt="Already have an account?" href="/signin" cta="Sign in" />
        </form>
      </Card>
    </>
  );
}
