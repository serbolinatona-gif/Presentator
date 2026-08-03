import { motion } from "framer-motion";
import { Check, Copy, Download, RotateCcw, Share2, Timer } from "lucide-react";
import { useState } from "react";
import { pptxDownloadUrl, previewUrl, shareUrl } from "../utils/api";
import { t } from "../utils/i18n";

export default function ResultView({ lang, result, onStartOver }) {
  const [copied, setCopied] = useState(false);

  const handleShare = async () => {
    const url = shareUrl(result.id);
    const text = `${t(lang, "shareText")} ${url}`;
    if (navigator.share) {
      try {
        await navigator.share({ title: "Presentator", text, url });
        return;
      } catch {
        // пользователь отменил — упадём на копирование
      }
    }
    await navigator.clipboard.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5 }}
      className="max-w-4xl mx-auto mt-10 px-4"
    >
      <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
        <div className="inline-flex items-center gap-1.5 text-sm text-slate-500 dark:text-slate-400">
          <Timer size={15} />
          {t(lang, "resultReady")} {result.elapsed_seconds} {t(lang, "seconds")}
        </div>
        <button
          onClick={onStartOver}
          className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-brand-600 dark:text-slate-400"
        >
          <RotateCcw size={14} /> {t(lang, "startOver")}
        </button>
      </div>

      <div className="rounded-2xl overflow-hidden border border-slate-200 dark:border-slate-800 shadow-sm bg-black">
        <iframe
          title="presentation-preview"
          src={previewUrl(result.id)}
          className="w-full aspect-video"
        />
      </div>

      <div className="flex flex-wrap gap-3 mt-5">
        <a
          href={pptxDownloadUrl(result.id)}
          className="inline-flex items-center gap-2 rounded-xl bg-brand-600 hover:bg-brand-700 text-white font-semibold px-5 py-2.5 transition"
        >
          <Download size={16} /> {t(lang, "downloadPptx")}
        </a>
        <button
          onClick={handleShare}
          className="inline-flex items-center gap-2 rounded-xl border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-200 font-semibold px-5 py-2.5 hover:border-brand-400 transition"
        >
          {copied ? <Check size={16} className="text-green-500" /> : <Share2 size={16} />}
          {copied ? t(lang, "linkCopied") : t(lang, "shareLink")}
        </button>
      </div>
    </motion.div>
  );
}
