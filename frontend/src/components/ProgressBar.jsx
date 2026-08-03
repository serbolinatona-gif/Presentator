import { motion } from "framer-motion";
import { Loader2 } from "lucide-react";

export default function ProgressBar({ progress = 0, message = "" }) {
  return (
    <div className="w-full max-w-xl mx-auto mt-6">
      <div className="flex items-center gap-2 mb-2 text-sm text-slate-600 dark:text-slate-300">
        <Loader2 size={16} className="animate-spin text-brand-600" />
        <span>{message}</span>
      </div>
      <div className="w-full h-2.5 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">
        <motion.div
          className="h-full rounded-full bg-gradient-to-r from-brand-500 to-brand-700"
          initial={{ width: "0%" }}
          animate={{ width: `${Math.min(progress, 100)}%` }}
          transition={{ duration: 0.4, ease: "easeOut" }}
        />
      </div>
    </div>
  );
}
