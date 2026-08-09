/** Shared motion primitives (framer-motion). */
import { motion, AnimatePresence, type HTMLMotionProps } from "framer-motion";
import { ReactNode } from "react";

export const fadeUp = {
  initial: { opacity: 0, y: 24 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -12 },
};

export const fadeIn = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
};

export const scaleIn = {
  initial: { opacity: 0, scale: 0.96 },
  animate: { opacity: 1, scale: 1 },
  exit: { opacity: 0, scale: 0.98 },
};

export const staggerContainer = {
  animate: { transition: { staggerChildren: 0.08, delayChildren: 0.05 } },
};

export const spring = { type: "spring" as const, stiffness: 380, damping: 32 };

export function MotionPage({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
    >
      {children}
    </motion.div>
  );
}

export function MotionCard({
  children,
  className = "",
  delay = 0,
  ...rest
}: HTMLMotionProps<"div"> & { delay?: number }) {
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-40px" }}
      transition={{ duration: 0.55, delay, ease: [0.16, 1, 0.3, 1] }}
      whileHover={{ y: -4, transition: { duration: 0.2 } }}
      {...rest}
    >
      {children}
    </motion.div>
  );
}

export function MotionButton({
  children,
  className = "",
  ...rest
}: HTMLMotionProps<"button">) {
  return (
    <motion.button
      className={className}
      whileHover={{ scale: 1.02 }}
      whileTap={{ scale: 0.98 }}
      transition={spring}
      {...rest}
    >
      {children}
    </motion.button>
  );
}

export { motion, AnimatePresence };
