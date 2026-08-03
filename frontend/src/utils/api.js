const API_BASE = import.meta.env.VITE_API_BASE || "/api";

/**
 * Запускает генерацию презентации и стримит события через SSE (fetch + ReadableStream,
 * т.к. нам нужен POST-запрос, а EventSource поддерживает только GET).
 *
 * onEvent(eventName, payload) вызывается для каждого полученного события:
 *   status, outline, slide, done, error
 */
export async function generatePresentation(params, onEvent, signal) {
  const response = await fetch(`${API_BASE}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
    signal,
  });

  if (!response.ok || !response.body) {
    throw new Error("Не удалось начать генерацию презентации.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";

    for (const chunk of chunks) {
      if (!chunk.trim()) continue;
      const lines = chunk.split("\n");
      let eventName = "message";
      let data = "";
      for (const line of lines) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data) {
        try {
          onEvent(eventName, JSON.parse(data));
        } catch {
          // игнорируем некорректные чанки
        }
      }
    }
  }
}

export function pptxDownloadUrl(id) {
  return `${API_BASE}/presentations/${id}/pptx`;
}

export function previewUrl(id) {
  return `${API_BASE}/presentations/${id}`;
}

export function shareUrl(id) {
  return `${window.location.origin}${API_BASE}/presentations/${id}`;
}
