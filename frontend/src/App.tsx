import {
  Navigate,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import ReviewerQueue from "./views/ReviewerQueue";
import CaseDetail from "./views/CaseDetail";
import Signup from "./views/vendor/Signup";
import Login from "./views/vendor/Login";
import Wizard from "./views/vendor/Wizard";
import Status from "./views/vendor/Status";
import VendorShell from "./views/vendor/VendorShell";
import Landing from "./views/Landing";
import { MotionPage } from "./components/motion";
import {
  EnvChip,
  HomeButton,
  NavLink,
  UserChip,
  Wordmark,
} from "./components/layout/Header";
import { AuthPage, HomeRedirect, OpsGate } from "./components/layout/Nav";
import { api, Me } from "./api";

export default function App() {
  const loc = useLocation();
  const isHome = loc.pathname === "/home";

  const health = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    refetchInterval: 15_000,
    retry: 0,
  });
  const meQ = useQuery({
    queryKey: ["me"],
    queryFn: api.me,
    retry: 0,
    refetchOnWindowFocus: true,
  });
  const me: Me | null = meQ.data ?? null;
  const isOps = me?.role === "ops";
  const isVendor = me?.role === "vendor";

  return (
    <motion.div
      className="min-h-full text-surface-900 flex flex-col"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.35 }}
    >
      <header
        className={
          "sticky top-0 z-30 border-b transition-colors duration-300 " +
          (isHome
            ? "border-white/10 bg-black/40 backdrop-blur-xl"
            : "border-surface-200 bg-white/90 backdrop-blur-xl")
        }
      >
        <div className="mx-auto max-w-7xl px-4 sm:px-6 h-16 flex items-center gap-3 sm:gap-6">
          <Wordmark
            variant={me?.role === "vendor" ? "vendor" : "ops"}
            onDark={isHome}
          />
          {!isHome && (
            <nav className="hidden md:flex items-center gap-1 text-sm">
              {(isOps || !me) && (
                <>
                  <NavLink to="/queue" active={loc.pathname.startsWith("/queue")}>
                    Ops dashboard
                  </NavLink>
                  </>
              )}
              {isVendor && (
                <>
                  <NavLink to="/m/onboard" active={loc.pathname === "/m/onboard"}>
                    Onboarding
                  </NavLink>
                  </>
              )}
            </nav>
          )}
          <motion.div
            className="ml-auto flex items-center gap-2"
            initial={{ opacity: 0, x: 12 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.15, duration: 0.4 }}
          >
            <HomeButton onDark={isHome} />
            <EnvChip
              health={health.data}
              loading={health.isLoading}
              error={!!health.error}
              onDark={isHome}
            />
            <UserChip me={me} onDark={isHome} />
          </motion.div>
        </div>
      </header>

      <main
        className={
          isHome
            ? "flex-1"
            : "flex-1 mx-auto max-w-7xl w-full px-4 sm:px-6 py-8"
        }
      >
        <AnimatePresence mode="wait">
          <Routes location={loc} key={loc.pathname}>
            <Route path="/" element={<HomeRedirect me={me} loaded={!meQ.isLoading} />} />
            <Route path="/home" element={<MotionPage><Landing /></MotionPage>} />

            <Route path="/m/signup" element={<AuthPage><Signup /></AuthPage>} />
            <Route path="/m/login" element={<AuthPage><Login /></AuthPage>} />

            <Route path="/m" element={<VendorShell />}>
              <Route path="onboard" element={<Wizard />} />
              </Route>
            <Route path="/m/status/:caseId" element={<Status />} />

            <Route
              path="/queue"
              element={
                <OpsGate me={me} loaded={!meQ.isLoading}>
                  <ReviewerQueue />
                </OpsGate>
              }
            />
            <Route
              path="/case/:caseId"
              element={
                <OpsGate me={me} loaded={!meQ.isLoading}>
                  <CaseDetail />
                </OpsGate>
              }
            />
            <Route path="*" element={<Navigate to="/home" replace />} />
          </Routes>
        </AnimatePresence>
      </main>

      {!isHome && (
        <footer className="mx-auto max-w-7xl w-full px-6 py-8 text-xs text-surface-500 flex items-center gap-3">
          <span>MOA · Vendor Onboarding Agents</span>
          <span className="text-surface-300">|</span>
          <span>Zamp · vendor onboarding & verification</span>
        </footer>
      )}
    </motion.div>
  );
}
