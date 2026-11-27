"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import { Spinner } from "@/components/ui/Spinner";

/** "/" only sends the visitor on, depending on the session. */
export default function Home() {
  const { status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status !== "loading") {
      router.replace(status === "authenticated" ? "/dashboard" : "/login");
    }
  }, [status, router]);

  return <Spinner fullScreen />;
}
