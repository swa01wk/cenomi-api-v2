"use client";

/**
 * Controls styled after the platform's SR form (screenshots of
 * newservicerequest?service_category=FIT_OUT_AND_HANDOVER): square inputs with
 * a grey border, searchable selects with a search icon and a divided chevron,
 * locked unit-code chips, and DD/MM/YYYY hh:mm date-time fields.
 */

import { forwardRef, useEffect, useMemo, useRef, useState } from "react";

export const PURPLE = "#4B2A7B";

export function fieldBox(opts: { disabled?: boolean; highlight?: boolean; missing?: boolean }) {
  return [
    "w-full min-h-[56px] border px-4 text-[15px] text-slate-800 outline-none transition",
    opts.disabled ? "bg-[#f3f3f5] border-[#d9d9de] cursor-not-allowed" : "bg-white border-[#a9a9b3]",
    opts.highlight ? "ring-2 ring-[#C9A84C] ring-offset-2 ring-offset-white" : "",
    opts.missing && !opts.highlight ? "border-rose-400" : "",
  ].join(" ");
}

export function FieldLabel({ text, required }: { text: string; required?: boolean }) {
  return (
    <div className="text-[17px] font-semibold text-[#4a4a55] mb-3">
      {text}
      {required && <span className="text-[#b3261e]"> *</span>}
    </div>
  );
}

function SearchIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#4a4a55" strokeWidth="1.8">
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}

function Chevron() {
  return (
    <span className="flex items-center gap-3 pl-3 border-l border-[#c9c9d0] h-8">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#8a8a94" strokeWidth="2">
        <path d="m6 9 6 6 6-6" />
      </svg>
    </span>
  );
}

export interface SelectOption {
  value: string;
  label: string;
}

/** Searchable single select — the platform's react-select look. */
export const SearchSelect = forwardRef<
  HTMLDivElement,
  {
    value: string;
    options: SelectOption[];
    placeholder: string;
    disabled?: boolean;
    highlight?: boolean;
    missing?: boolean;
    onChange: (value: string) => void;
  }
>(function SearchSelect({ value, options, placeholder, disabled, highlight, missing, onChange }, ref) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const root = useRef<HTMLDivElement>(null);
  const selected = options.find((o) => o.value === value);
  const filtered = useMemo(
    () => options.filter((o) => o.label.toLowerCase().includes(query.toLowerCase())),
    [options, query]
  );

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  return (
    <div
      ref={(el) => {
        root.current = el;
        if (typeof ref === "function") ref(el);
        else if (ref) ref.current = el;
      }}
      className="relative"
    >
      <div
        className={`${fieldBox({ disabled, highlight, missing })} flex items-center gap-3 ${
          open ? "border-[#4B2A7B]" : ""
        }`}
        onClick={() => !disabled && setOpen(true)}
      >
        <SearchIcon />
        <input
          className="flex-1 min-w-0 bg-transparent outline-none placeholder:text-[#8a8a94] disabled:cursor-not-allowed"
          placeholder={selected ? "" : placeholder}
          value={open ? query : selected?.label ?? ""}
          disabled={disabled}
          onFocus={() => {
            setQuery("");
            setOpen(true);
          }}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Chevron />
      </div>
      {open && !disabled && (
        <ul className="absolute z-30 left-0 right-0 mt-1 max-h-64 overflow-y-auto bg-white border border-[#c9c9d0] shadow-lg">
          {filtered.length === 0 && <li className="px-4 py-3 text-sm text-slate-400">No options</li>}
          {filtered.map((o) => (
            <li
              key={o.value}
              className={`px-4 py-3 text-[15px] cursor-pointer hover:bg-[#f1ecf8] ${
                o.value === value ? "bg-[#ebe4f5] text-[#4B2A7B]" : "text-slate-800"
              }`}
              onMouseDown={(e) => {
                e.preventDefault();
                onChange(o.value);
                setOpen(false);
              }}
            >
              {o.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
});

/** Unit codes come from the lease and stay fixed — shown as chips, no removal. */
export function UnitCodeChips({ codes, highlight }: { codes: string[]; highlight?: boolean }) {
  return (
    <div className={`${fieldBox({ disabled: true, highlight })} flex items-center gap-3 flex-wrap py-2`}>
      <SearchIcon />
      {codes.length === 0 ? (
        <span className="flex-1 text-[#8a8a94]">Choose unit codes</span>
      ) : (
        <span className="flex-1 flex flex-wrap gap-2">
          {codes.map((c) => (
            <span key={c} className="px-2.5 py-1 bg-[#e6e1ee] text-[#3b2a55] text-sm rounded-sm">
              {c}
            </span>
          ))}
        </span>
      )}
      <Chevron />
    </div>
  );
}

function ClockPlus() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke={PURPLE} strokeWidth="1.8">
      <path d="M20 12a8 8 0 1 1-8-8" />
      <path d="M12 8v4l2.5 2.5" />
      <path d="M18 3v4M16 5h4" />
    </svg>
  );
}

const DISPLAY_RE = /^(\d{1,2})\/(\d{1,2})\/(\d{4})(?:\s+(\d{1,2}):(\d{2}))?$/;

/** "2026-10-15" + "09:30" → "15/10/2026 09:30". */
export function toDisplay(date?: string, time?: string): string {
  if (!date) return "";
  const [y, m, d] = date.slice(0, 10).split("-");
  if (!y || !m || !d) return "";
  return `${d}/${m}/${y} ${time || "09:00"}`;
}

/** "15/10/2026 9:30" → { date: "2026-10-15", time: "09:30" }, or null. */
export function fromDisplay(text: string): { date: string; time: string } | null {
  const m = DISPLAY_RE.exec(text.trim());
  if (!m) return null;
  const [, d, mo, y, h = "9", mi = "00"] = m;
  const date = `${y}-${mo.padStart(2, "0")}-${d.padStart(2, "0")}`;
  if (Number.isNaN(Date.parse(date))) return null;
  return { date, time: `${h.padStart(2, "0")}:${mi}` };
}

/**
 * DD/MM/YYYY hh:mm, typed or picked. Commits on blur (typed) or on pick, so
 * each change is one agent turn at most.
 */
export const DateTimeField = forwardRef<
  HTMLDivElement,
  {
    date?: string;
    time?: string;
    disabled?: boolean;
    highlight?: boolean;
    missing?: boolean;
    onCommit: (value: { date: string; time: string }) => void;
  }
>(function DateTimeField({ date, time, disabled, highlight, missing, onCommit }, ref) {
  const shown = toDisplay(date, time);
  const [text, setText] = useState(shown);
  const [invalid, setInvalid] = useState(false);
  const picker = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setText(shown);
    setInvalid(false);
  }, [shown]);

  const commitText = () => {
    if (text.trim() === shown || !text.trim()) {
      setText(shown);
      return;
    }
    const parsed = fromDisplay(text);
    if (!parsed) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    onCommit(parsed);
  };

  return (
    <div ref={ref}>
      <div className={`${fieldBox({ disabled, highlight, missing: missing || invalid })} flex items-center`}>
        <input
          className="flex-1 min-w-0 bg-transparent outline-none placeholder:text-[#8a8a94] disabled:cursor-not-allowed"
          placeholder="DD/MM/YYYY hh:mm"
          value={text}
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          onBlur={commitText}
          onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        />
        <button
          type="button"
          aria-label="Pick date and time"
          disabled={disabled}
          className="ml-2 disabled:opacity-40"
          onClick={() => {
            const el = picker.current;
            if (!el) return;
            try {
              el.showPicker();
            } catch {
              el.focus();
            }
          }}
        >
          <ClockPlus />
        </button>
        <input
          ref={picker}
          type="datetime-local"
          tabIndex={-1}
          aria-hidden
          className="sr-only"
          value={date ? `${date.slice(0, 10)}T${time || "09:00"}` : ""}
          onChange={(e) => {
            const [d, t] = e.target.value.split("T");
            if (d && t) onCommit({ date: d, time: t.slice(0, 5) });
          }}
        />
      </div>
      {invalid && <div className="mt-1 text-xs text-rose-600">Use DD/MM/YYYY hh:mm</div>}
    </div>
  );
});
