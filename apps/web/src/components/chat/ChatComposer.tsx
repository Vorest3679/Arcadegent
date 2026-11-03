import { useEffect, useRef, type FormEvent, type KeyboardEvent } from "react";
import { ArrowUp, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useAppStore } from "@/stores/appStore";

export const CHAT_PLACEHOLDER = "尽管问机厅相关问题";

// Enter sends, Shift+Enter breaks the line; IME composition never sends.
export function ChatComposer({ busy, sending, onSubmit }: {
  busy: boolean;
  sending: boolean;
  onSubmit: (event: FormEvent) => Promise<void>;
}) {
  const inputValue = useAppStore((state) => state.inputValue);
  const setInputValue = useAppStore((state) => state.setInputValue);
  const formRef = useRef<HTMLFormElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Grow with the text up to a few lines.
  useEffect(() => {
    const element = textareaRef.current;
    if (!element) {
      return;
    }
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 168)}px`;
  }, [inputValue]);

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      formRef.current?.requestSubmit();
    }
  }

  const label = sending ? "发送中" : busy ? "处理中" : "发送";
  return (
    <form
      ref={formRef}
      data-testid="chat-composer"
      onSubmit={(event) => void onSubmit(event)}
      className="flex items-end gap-2 rounded-2xl border bg-card p-2 shadow-md focus-within:border-primary/50 focus-within:ring-[3px] focus-within:ring-ring/20"
    >
      <Textarea
        ref={textareaRef}
        rows={1}
        value={inputValue}
        onChange={(event) => setInputValue(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder={CHAT_PLACEHOLDER}
        disabled={busy}
        className="max-h-42 min-h-11 flex-1 resize-none border-0 bg-transparent px-2 py-2.5 text-base shadow-none focus-visible:ring-0 md:text-[0.95rem]"
      />
      <Button
        type="submit"
        size="icon"
        aria-label={label}
        title={label}
        disabled={busy || inputValue.trim().length === 0}
        className="size-11 shrink-0 rounded-xl"
      >
        {busy ? <Loader2 className="size-5 animate-spin" aria-hidden="true" /> : <ArrowUp className="size-5" aria-hidden="true" />}
      </Button>
    </form>
  );
}
