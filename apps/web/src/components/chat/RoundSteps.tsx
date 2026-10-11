import { useState } from "react";
import { useDelayedUnmount } from "../../hooks/useDelayedUnmount";
import type { RoundStep } from "../../lib/chatRounds";
import { formatAgentName, formatToolName } from "../../lib/sse/chatStream";
import { MarkdownMessage } from "../MarkdownMessage";
import { StepStatusIcon } from "./StepStatusIcon";

function summarize(steps: RoundStep[]): string {
  const tools = steps.filter((step) => step.kind === "tool").length;
  const texts = steps.length - tools;
  const parts = [];
  if (tools > 0) {
    parts.push(`调用了 ${tools} 个工具`);
  }
  if (texts > 0) {
    parts.push(`${texts} 段中间回复`);
  }
  return parts.join(" · ");
}

// Process area of one round: open while the round runs, a one-line summary
// afterwards. A manual toggle wins over that default.
export function RoundSteps({ steps, running }: { steps: RoundStep[]; running: boolean }) {
  const [override, setOverride] = useState<boolean | null>(null);
  const open = override ?? running;
  const { mounted, closing } = useDelayedUnmount(open);
  return (
    <div className="chat-bubble round-steps">
      <button
        type="button"
        className="round-steps-toggle"
        aria-expanded={open}
        onClick={() => setOverride(!open)}
      >
        <span>{summarize(steps)}</span>
        <span aria-hidden="true">{open ? "收起" : "展开"}</span>
      </button>
      {mounted ? (
        <div className={`round-steps-collapse${closing ? " is-closing" : ""}`}>
        <ul className="round-steps-list">
          {steps.map((step, index) =>
            step.kind === "tool" ? (
              <li key={`tool-${step.callId}`} className="step-row step-tool" title={step.name}>
                <span className="step-tool-name">{formatToolName(step.name)}</span>
                {step.agent ? <span className="step-tool-agent">{formatAgentName(step.agent)}</span> : null}
                <StepStatusIcon status={step.status} />
              </li>
            ) : (
              <li key={`text-${index}`} className="step-row step-text">
                <MarkdownMessage content={step.content} />
              </li>
            )
          )}
        </ul>
        </div>
      ) : null}
    </div>
  );
}
