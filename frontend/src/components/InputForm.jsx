import { motion } from "framer-motion";
import { Sparkles } from "lucide-react";
import { useEffect, useRef } from "react";
import { t } from "../utils/i18n";

const STYLES = ["minimal", "academic", "creative", "corporate", "dark"];

export default function InputForm({
  lang,
  topic,
  setTopic,
  slideCount,
  setSlideCount,
  style,
  setStyle,
  detailedPrompt,
  setDetailedPrompt,
  isGenerating,
  onGenerate,
}) {
  const textareaRef = useRef(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    }
  }, [topic]);

  const examples = [
    t(lang, "example1"),
    t(lang, "example2"),
    t(lang, "example3"),
  ];

  const rangeProgress = ((slideCount - 5) / (25 - 5)) * 100;

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: 0.15 }}
      className="max-w-2xl mx-auto mt-10 px-4"
    >
      <div className="rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm p-5 sm:p-6">
        <textarea
          ref={textareaRef}
          rows={2}
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder={t(lang, "topicPlaceholder")}
          className="w-full resize-none bg-transparent text-lg text-slate-900 dark:text-white placeholder-slate-400 outline-none"
        />

        <div className="flex flex-wrap gap-2 mt-3">
          <span className="text-xs text-slate-400 self-center mr-1">{t(lang, "examplesLabel")}</span>
          {examples.map((ex) => (
            <button
              key={ex}
              type="button"
              onClick={() => setTopic(ex)}
              className="text-xs px-3 py-1 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-brand-50 hover:text-brand-700 dark:hover:bg-brand-900/40 transition"
            >
              {ex}
            </button>
          ))}
        </div>

        <div className="grid sm:grid-cols-2 gap-5 mt-6">
          <div>
            <label className="flex justify-between text-sm text-slate-600 dark:text-slate-300 mb-2">
              <span>{t(lang, "slideCountLabel")}</span>
              <span className="font-semibold text-brand-600">{slideCount}</span>
            </label>
            <input
              type="range"
              min={5}
              max={25}
              value={slideCount}
              onChange={(e) => setSlideCount(Number(e.target.value))}
              style={{ "--range-progress": `${rangeProgress}%` }}
              className="w-full"
            />
          </div>

          <div>
            <label className="text-sm text-slate-600 dark:text-slate-300 mb-2 block">
              {t(lang, "styleLabel")}
            </label>
            <div className="grid grid-cols-3 gap-1.5">
              {STYLES.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setStyle(s)}
                  className={`text-xs px-2 py-1.5 rounded-lg border transition ${
                    style === s
                      ? "border-brand-500 bg-brand-50 dark:bg-brand-900/40 text-brand-700 dark:text-brand-300"
                      : "border-slate-200 dark:border-slate-700 text-slate-500 dark:text-slate-400 hover:border-brand-300"
                  }`}
                >
                  {t(lang, `style${s.charAt(0).toUpperCase() + s.slice(1)}`)}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="mt-5">
          <label className="text-sm text-slate-600 dark:text-slate-300 mb-2 block">
            {t(lang, "detailedPromptLabel")}
          </label>
          <textarea
            rows={2}
            value={detailedPrompt}
            onChange={(e) => setDetailedPrompt(e.target.value)}
            placeholder={t(lang, "detailedPromptPlaceholder")}
            maxLength={1000}
            className="w-full resize-none rounded-xl border border-slate-200 dark:border-slate-700 bg-transparent text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 p-3 outline-none focus:border-brand-400"
          />
        </div>

        <button
          onClick={onGenerate}
          disabled={isGenerating || topic.trim().length < 3}
          className="mt-6 w-full flex items-center justify-center gap-2 rounded-xl bg-brand-600 hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-3 transition"
        >
          <Sparkles size={18} />
          {isGenerating ? t(lang, "generatingBtn") : t(lang, "generateBtn")}
        </button>
      </div>
    </motion.div>
  );
}
