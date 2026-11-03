import { ExternalLink, Navigation } from "lucide-react";
import { Button } from "@/components/ui/button";

// Links that open a place or route in AMap (web or app).
export type MapAction = {
  key: string;
  label: string;
  href: string;
  emphasis?: "primary" | "secondary";
};

export function MapActionBar({ actions }: { actions: MapAction[] }) {
  if (!actions.length) {
    return null;
  }

  return (
    <div className="flex flex-wrap gap-2 py-1">
      {actions.map((action) => {
        const primary = action.emphasis === "primary";
        return (
          <Button key={action.key} asChild variant={primary ? "default" : "outline"} className={primary ? undefined : "bg-card"}>
            <a href={action.href} target="_blank" rel="noreferrer" data-testid={`map-action-${action.key}`}>
              {primary ? <Navigation aria-hidden="true" /> : <ExternalLink aria-hidden="true" />}
              {action.label}
            </a>
          </Button>
        );
      })}
    </div>
  );
}
