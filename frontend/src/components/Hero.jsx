import { motion } from "framer-motion";
import { Sparkles } from "lucide-react";
import { t } from "../utils/i18n";

export default function Hero({ lang }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5 }}
      className="text-center max-w-2xl mx-auto px-4"
    >
      <div className="flex justify-center gap-2 mb-4">
        <span className="inline-flex items-center gap-1 rounded-full bg-brand-50 dark:bg-brand-900/40 text-brand-700 dark:text-brand-300 text-xs font-medium px-3 py-1">
          <Sparkles size={12} /> {t(lang, "badgeFree")}
        </span>
        <span className="inline-flex items-center rounded-full bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 text-xs font-medium px-3 py-1">
          {t(lang, "badgeNoSignup")}
        </span>
      </div>
      <h1 className="text-3xl sm:text-5xl font-extrabold tracking-tight text-slate-900 dark:text-white">
        {t(lang, "heroTitle")}
      </h1>
      <p className="mt-4 text-base sm:text-lg text-slate-500 dark:text-slate-400">
        {t(lang, "heroSubtitle")}
      </p>
    </motion.div>
  );
}
