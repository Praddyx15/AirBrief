import * as React from "react";
import { cn } from "@/lib/utils";

/** Shared input styling (16 px text so iOS does not zoom; invalid state from aria-invalid). */
export const inputClassName = cn(
  "flex h-9 w-full min-w-0 rounded border border-border bg-bg px-3 py-1 text-base text-fg",
  "placeholder:text-fg-muted transition-colors duration-200",
  "hover:border-fg-subtle focus-visible:border-accent",
  "disabled:cursor-not-allowed disabled:opacity-50",
  "aria-[invalid=true]:border-danger",
  "file:mr-3 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-fg",
  "pointer-coarse:min-h-11",
);

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

export const Input = React.forwardRef<HTMLInputElement, InputProps>(({ className, type = "text", ...props }, ref) => (
  <input ref={ref} type={type} className={cn(inputClassName, className)} {...props} />
));
Input.displayName = "Input";

export type TextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement>;

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(({ className, ...props }, ref) => (
  <textarea ref={ref} className={cn(inputClassName, "h-auto min-h-20 py-2", className)} {...props} />
));
Textarea.displayName = "Textarea";
