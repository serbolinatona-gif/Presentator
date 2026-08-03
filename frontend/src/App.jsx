import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, Globe, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import Hero from "./components/Hero.jsx";
import InputForm from "./components/InputForm.jsx";
import ProgressBar from "./components/ProgressBar.jsx";
import ResultView from "./components/ResultView.jsx";
import { generatePresentation } from "./utils/api.js";
import { t } from "./utils/i18n.js";

export default function App() {
  const [lang, setLang] = useState(() => localStorage.getItem("sf_lang") || "ru");
  const [dark, setDark] = useState(() => localStorage.getItem("sf_theme") === "dark");

  const [topic, setTopic] = useState("");
  const [slideCount, setSlideCount] = useState(10);
  const [style, setStyle] = useState("minimal");
  const [detailedPrompt, setDetailedPrompt] = useState("");

  const [isGenerating, setIsGenerating] = useState(false);
  const [progress, setProgress] = useState(0);
  const [statusMessage, setStatusMessage] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("sf_theme", dark ? "dark" : "light");
  }, [dark]);

  useEffect(() => {
    localStorage.setItem("sf_lang", lang);
    document.documentElement.lang = lang;
  }, [lang]);

  const handleGenerate = async () => {
    if (topic.trim().length < 3) return;
    setIsGenerating(true);
    setError(null);
    setResult(null);
    setProgress(5);
    setStatusMessage(t(lang, "stageOutline"));

    try {
      await generatePresentation(
        { topic, slide_count: slideCount, style, language: lang, with_notes: false, detailed_prompt: detailedPrompt || null },
        (eventName, payload) => {
          if (eventName === "status") {
            setStatusMessage(payload.message);
            if (payload.progress) setProgress(payload.progress);
          } else if (eventName === "slide") {
            setProgress((p) => Math.min(p + 70 / slideCount, 95));
          } else if (eventName === "done") {
            setProgress(100);
            setResult(payload);
            setIsGenerating(false);
          } else if (eventName === "error") {
            setError(payload.message);
            setIsGenerating(false);
          }
        }
      );
    } catch (e) {
      setError(e.message || "Не удалось связаться с сервером.");
      setIsGenerating(false);
    }
  };

  const handleStartOver = () => {
    setResult(null);
    setError(null);
    setProgress(0);
  };

  return (
    <div className="min-h-screen bg-white dark:bg-slate-950 transition-colors">
      <header className="flex items-center justify-between max-w-5xl mx-auto px-4 py-5">
        <div className="font-bold text-lg text-slate-900 dark:text-white">Presentator</div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setLang(lang === "ru" ? "en" : "ru")}
            className="inline-flex items-center gap-1 text-sm px-3 py-1.5 rounded-full border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:border-brand-400 transition"
          >
            <Globe size={14} /> {lang.toUpperCase()}
          </button>
          <button
            onClick={() => setDark(!dark)}
            className="p-2 rounded-full border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:border-brand-400 transition"
            aria-label="Toggle theme"
          >
            {dark ? <Sun size={16} /> : <Moon size={16} />}
          </button>
        </div>
      </header>

      <main className="pb-20">
        <div className="pt-8 sm:pt-14">
          <Hero lang={lang} />
        </div>

        <AnimatePresence mode="wait">
          {!result && (
            <motion.div key="form" exit={{ opacity: 0, y: -10 }}>
              <InputForm
                lang={lang}
                topic={topic}
                setTopic={setTopic}
                slideCount={slideCount}
                setSlideCount={setSlideCount}
                style={style}
                setStyle={setStyle}
                detailedPrompt={detailedPrompt}
                setDetailedPrompt={setDetailedPrompt}
                isGenerating={isGenerating}
                onGenerate={handleGenerate}
              />

              {isGenerating && (
                <ProgressBar progress={progress} message={statusMessage} />
              )}

              {error && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  className="max-w-xl mx-auto mt-6 px-4"
                >
                  <div className="flex items-start gap-3 rounded-xl border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-950/30 p-4 text-sm text-red-700 dark:text-red-300">
                    <AlertTriangle size={18} className="shrink-0 mt-0.5" />
                    <div>
                      <div className="font-semibold">{t(lang, "errorTitle")}</div>
                      <div className="mt-1">{error}</div>
                      <button
                        onClick={handleGenerate}
                        className="mt-2 underline font-medium"
                      >
                        {t(lang, "tryAgain")}
                      </button>
                    </div>
                  </div>
                </motion.div>
              )}
            </motion.div>
          )}

          {result && (
            <motion.div key="result" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
              <ResultView lang={lang} result={result} onStartOver={handleStartOver} />
            </motion.div>
          )}
        </AnimatePresence>
      </main>

      <footer className="text-center text-xs text-slate-400 pb-8 space-y-1">
        <div>{t(lang, "footerNote")}</div>
        <div>{t(lang, "footerAuthor")}</div>
      </footer>
    </div>
  );
}
