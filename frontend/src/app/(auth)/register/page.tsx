import { AuthForm } from "@/components/auth/AuthForm";

// The layout shows nothing of the page until the session is known (it exists only in the
// browser), so navigating here is not "instant" by design.
export const instant = false;

export default function RegisterPage() {
  return <AuthForm mode="register" />;
}
