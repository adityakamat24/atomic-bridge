"use client";

import { KeyboardEvent, useEffect, useRef, useState } from "react";

export function MessageInput({
  onSubmit,
  disabled,
}: {
  onSubmit: (text: string) => void;
  disabled?: boolean;
}) {
  const [text, setText] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize the textarea up to 6 lines.
  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "0px";
    const next = Math.min(el.scrollHeight, 24 * 6 + 12);
    el.style.height = `${next}px`;
  }, [text]);

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onSubmit(trimmed);
    setText("");
  };

  const handleKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="border-t border-line bg-bg">
      <div className="mx-auto max-w-3xl px-6 py-4">
        <div className="flex items-start gap-3 rounded-md border border-line bg-bg-1 p-3 transition focus-within:border-line-strong">
          <span
            aria-hidden
            className="select-none pt-px font-mono text-sm text-fg-4"
          >
            &gt;
          </span>
          <textarea
            ref={taRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKey}
            rows={1}
            placeholder="Ask about tickets, users, teams, knowledge…"
            disabled={disabled}
            className="flex-1 resize-none bg-transparent text-[15px] leading-relaxed text-fg outline-none placeholder:text-fg-4 disabled:opacity-50"
            style={{ height: "24px" }}
          />
          <button
            onClick={submit}
            disabled={disabled || !text.trim()}
            aria-label="Send"
            className="flex h-7 w-7 items-center justify-center rounded border border-line text-fg-3 transition hover:border-brand-line hover:bg-brand-soft hover:text-brand disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:border-line disabled:hover:bg-transparent disabled:hover:text-fg-3"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M5 12h14" />
              <path d="m13 6 6 6-6 6" />
            </svg>
          </button>
        </div>
        <div className="mt-2 px-1 text-2xs text-fg-4 font-mono">
          enter ↵ to send · shift+enter for newline
          {disabled && (
            <span className="ml-2 text-brand">· Atom is planning…</span>
          )}
        </div>
      </div>
    </div>
  );
}
