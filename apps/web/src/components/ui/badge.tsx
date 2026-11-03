import * as React from "react"
import { cn } from "@/lib/utils"
import { Slot } from "radix-ui"

// 本地变体表，不依赖 class-variance-authority。
const badgeBase =
  "inline-flex w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-full border border-transparent px-2 py-0.5 text-xs font-medium whitespace-nowrap transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&>svg]:pointer-events-none [&>svg]:size-3"

const badgeVariantClasses = {
  default: "bg-primary text-primary-foreground [a&]:hover:bg-primary/90",
  secondary:
    "bg-secondary text-secondary-foreground [a&]:hover:bg-secondary/90",
  destructive:
    "bg-destructive text-white focus-visible:ring-destructive/20 dark:bg-destructive/60 dark:focus-visible:ring-destructive/40 [a&]:hover:bg-destructive/90",
  outline:
    "border-border text-foreground [a&]:hover:bg-accent [a&]:hover:text-accent-foreground",
  ghost: "[a&]:hover:bg-accent [a&]:hover:text-accent-foreground",
  link: "text-primary underline-offset-4 [a&]:hover:underline",
} as const

export type BadgeVariant = keyof typeof badgeVariantClasses

function Badge({
  className,
  variant = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"span"> & {
  variant?: BadgeVariant
  asChild?: boolean
}) {
  const Comp = asChild ? Slot.Root : "span"

  return (
    <Comp
      data-slot="badge"
      data-variant={variant}
      className={cn(badgeBase, badgeVariantClasses[variant], className)}
      {...props}
    />
  )
}

export { Badge }
