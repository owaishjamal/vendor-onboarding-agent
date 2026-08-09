import { Navigate } from "react-router-dom";
import type { ReactNode } from "react";
import { motion } from "framer-motion";
import { Me } from "../../api";
import { MotionPage } from "../motion";

export function HomeRedirect({ me, loaded }: { me: Me | null; loaded: boolean }) {
  if (!loaded) {
    return (
      <div className="py-24 flex justify-center">
        <motion.div
          className="h-8 w-8 rounded-full border-2 border-black border-t-transparent"
          animate={{ rotate: 360 }}
          transition={{ repeat: Infinity, duration: 0.9, ease: "linear" }}
        />
      </div>
    );
  }
  if (!me) return <Navigate to="/home" replace />;
  if (me.role === "vendor") return <Navigate to="/m/onboard" replace />;
  return <Navigate to="/queue" replace />;
}

export function OpsGate({
  me,
  loaded,
  children,
}: {
  me: Me | null;
  loaded: boolean;
  children: ReactNode;
}) {
  // Render a loader, not `null`. Returning null meant that opening /queue or
  // /case/:id directly — or refreshing either — painted an empty page for a
  // frame before the real content arrived, which reads as a broken route.
  if (!loaded) {
    return (
      <div className="py-24 flex justify-center" role="status" aria-label="Loading">
        <motion.div
          className="h-8 w-8 rounded-full border-2 border-black border-t-transparent"
          animate={{ rotate: 360 }}
          transition={{ repeat: Infinity, duration: 0.9, ease: "linear" }}
        />
      </div>
    );
  }
  if (me && me.role !== "ops") return <Navigate to="/m/onboard" replace />;
  return <MotionPage>{children}</MotionPage>;
}

export function AuthPage({ children }: { children: ReactNode }) {
  return <MotionPage>{children}</MotionPage>;
}
