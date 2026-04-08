import { useMemo } from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";

type MarkdownMessageProps = {
  content: string;
  className?: string;
};

function renderMarkdown(content: string): string {
  const rawHtml = marked.parse(content, {
    async: false,
    breaks: true,
    gfm: true
  }) as string;

  return DOMPurify.sanitize(rawHtml, {
    USE_PROFILES: { html: true }
  });
}

export function MarkdownMessage({ content, className }: MarkdownMessageProps) {
  const html = useMemo(() => renderMarkdown(content), [content]);
  const classes = ["chat-markdown", className].filter(Boolean).join(" ");

  return <div className={classes} dangerouslySetInnerHTML={{ __html: html }} />;
}
