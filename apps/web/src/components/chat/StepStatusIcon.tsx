import { STEP_STATUS_LABEL } from "../../lib/sse/chatStream";
import type { ToolStepStatus } from "../../lib/chatRounds";

export function StepStatusIcon({ status }: { status: ToolStepStatus }) {
  const label = STEP_STATUS_LABEL[status];
  return (
    <svg
      className={`step-status-icon is-${status}`}
      viewBox="0 0 20 20"
      width="18"
      height="18"
      role="img"
      aria-label={label}
    >
      <title>{label}</title>
      {status === "running" ? (
        <circle className="step-spinner" cx="10" cy="10" r="7" fill="none" strokeWidth="2.4" strokeLinecap="round" strokeDasharray="22 22" />
      ) : null}
      {status === "completed" ? (
        <path d="M5 10.5l3.3 3.3L15 7" fill="none" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      ) : null}
      {status === "failed" ? (
        <path d="M6 6l8 8M14 6l-8 8" fill="none" strokeWidth="2.4" strokeLinecap="round" />
      ) : null}
      {status === "cancelled" ? <path d="M5.5 10h9" fill="none" strokeWidth="2.4" strokeLinecap="round" /> : null}
    </svg>
  );
}
