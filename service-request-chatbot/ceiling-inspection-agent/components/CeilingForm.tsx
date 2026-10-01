"use client";

/**
 * The platform's Open / Close Ceiling Inspection form, beside the chat.
 * Layout, labels and placeholders follow the platform's New Service Request
 * page (formConfig.ts `open_ceiling_inspection` / `close_ceiling_inspection`).
 *
 * Two-way:
 *   chat → form  every turn's `form_state` fills the fields, rings the field
 *                the agent is asking for, and locks the form once the SR has
 *                left the create stage.
 *   form → chat  choosing the sub category and a lease starts the agent;
 *                after that a lease change is a visible "Use lease …" turn and
 *                a text / date edit rides back silently as corrected_fields on
 *                blur (never per keystroke — each one is an LLM turn).
 *
 * Company Name and Lease ID list only what the signed-in user can access
 * (GET /api/v1/lookups/*, scoped by the JWT's properties).
 */

import { useEffect, useRef, useState } from "react";
import { useCurrentUser } from "@/components/AuthGuard";
import {
  DateTimeField,
  FieldLabel,
  SearchSelect,
  UnitCodeChips,
  fieldBox,
} from "@/components/FormControls";
import { getCompanies, getFormOptions, getLeases } from "@/lib/api";
import type { CompanyOption, FormOptions, FormValues, LeaseOption } from "@/lib/types";
import type { CeilingSession } from "@/lib/useCeilingSession";

const CREATE_STAGE = "CREATE_CEILING_INSPECTION_SR";
const OTHER_DOC_TYPE = "FITOUT_INSPECTION_OTHER";
const SERVICE_CATEGORY = { value: "FIT_OUT_AND_HANDOVER", label: "Fit-out & Handover" };

type TextKey = "title" | "description" | "notes" | "comments";
/** Values sent with the opening turn if typed before the agent started. */
const PENDING_KEYS = [
  "title",
  "description",
  "startDate",
  "inspectionTime",
  "endDate",
  "inspectionEndTime",
  "notes",
  "comments",
] as const;

export function CeilingForm({ session }: { session: CeilingSession }) {
  const user = useCurrentUser();
  const isTenant = user.role === "TENANT";
  const { formState, busy, sessionId, turn } = session;

  const [options, setOptions] = useState<FormOptions | null>(null);
  const [companies, setCompanies] = useState<CompanyOption[]>([]);
  const [leases, setLeases] = useState<LeaseOption[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [subCategory, setSubCategory] = useState("");
  const [companyId, setCompanyId] = useState<number | null>(null);
  const [leaseCode, setLeaseCode] = useState("");
  const [values, setValues] = useState<FormValues>({});
  const [docType, setDocType] = useState(OTHER_DOC_TYPE);
  const [docComment, setDocComment] = useState("");
  const focused = useRef<string | null>(null);
  const fieldRefs = useRef<Record<string, HTMLElement | null>>({});

  const agentActive = Boolean(formState && formState.stage === CREATE_STAGE && sessionId);
  const locked = Boolean(formState?.locked);
  const editable = isTenant && !locked;

  // Options the user may pick from — fetched once per signed-in user.
  useEffect(() => {
    let cancelled = false;
    Promise.all([getFormOptions(), getCompanies()])
      .then(([opts, comps]) => {
        if (cancelled) return;
        setOptions(opts);
        setCompanies(comps);
        if (comps.length === 1) setCompanyId(comps[0].tenant_profile_id);
      })
      .catch((e) => !cancelled && setLoadError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [user.role]);

  useEffect(() => {
    if (companyId === null) {
      setLeases([]);
      return;
    }
    let cancelled = false;
    getLeases(companyId)
      .then((rows) => !cancelled && setLeases(rows))
      .catch((e) => !cancelled && setLoadError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [companyId]);

  // chat → form. The field being typed in keeps the user's text.
  useEffect(() => {
    if (!formState) {
      setValues({});
      setSubCategory("");
      setLeaseCode("");
      if (companies.length !== 1) setCompanyId(null);
      return;
    }
    const incoming = formState.values;
    setValues((prev) => {
      const next: FormValues = { ...prev, ...incoming };
      if (focused.current && focused.current in prev) {
        (next as Record<string, unknown>)[focused.current] = (prev as Record<string, unknown>)[focused.current];
      }
      return next;
    });
    if (formState.sub_category) setSubCategory(formState.sub_category);
    if (incoming.tenant_profile_id !== undefined) setCompanyId(Number(incoming.tenant_profile_id));
    if (incoming.lease_code) setLeaseCode(incoming.lease_code);
    // companies is only read for the single-company default above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [formState]);

  // Bring the field the agent is asking about into view.
  const activeField = formState && !locked ? formState.active_field : null;
  useEffect(() => {
    if (!activeField) return;
    fieldRefs.current[activeField]?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [activeField]);

  const missing = new Set(agentActive ? formState?.missing ?? [] : []);
  const subOptions = (options?.sub_categories ?? []).map((s) => ({ value: s.value, label: s.label }));
  const subLabel = subOptions.find((s) => s.value === subCategory)?.label ?? "";

  const pendingFields = (): Partial<FormValues> => {
    const out: Partial<FormValues> = {};
    for (const key of PENDING_KEYS) {
      if (values[key]) (out as Record<string, unknown>)[key] = values[key];
    }
    return out;
  };

  const startAgent = (sub: string, code: string) => {
    const label = subOptions.find((s) => s.value === sub)?.label ?? "ceiling inspection";
    const article = /^[aeiou]/i.test(label) ? "an" : "a";
    void turn({ message: `Raise ${article} ${label} for lease ${code}`, correctedFields: pendingFields() });
  };

  const onSubCategory = (sub: string) => {
    setSubCategory(sub);
    if (!agentActive && sub && leaseCode) startAgent(sub, leaseCode);
  };

  const onCompany = (id: number | null) => {
    setCompanyId(id);
    // Same as the platform: a new company clears the lease and what it filled.
    setLeaseCode("");
    setValues((v) => ({
      ...v,
      lease_code: undefined,
      lease_brand_mall: undefined,
      city: undefined,
      contracted_area: undefined,
      unit_codes: undefined,
    }));
  };

  const onLease = (code: string) => {
    setLeaseCode(code);
    const row = leases.find((l) => l.lease_code === code);
    if (row) {
      setValues((v) => ({
        ...v,
        lease_code: row.lease_code,
        lease_brand_mall: row.lease_brand_mall,
        city: row.city,
        contracted_area: row.contracted_area,
        unit_codes: row.unit_codes,
        company_name: row.company_name,
      }));
    }
    if (!code) return;
    if (agentActive) {
      if (code !== formState?.values.lease_code) {
        void turn({ message: `Use lease ${code}`, selectedLeaseId: code });
      }
    } else if (subCategory) {
      startAgent(subCategory, code);
    }
  };

  const sendIfActive = (patch: Partial<FormValues>) => {
    if (!agentActive) return;
    const changed = Object.entries(patch).some(
      ([k, v]) => (v ?? "") !== ((formState?.values as Record<string, unknown>)?.[k] ?? "")
    );
    if (changed) void turn({ message: "", correctedFields: patch });
  };

  const onBlurText = (key: TextKey) => {
    focused.current = null;
    const value = values[key];
    if (value === undefined) return;
    sendIfActive({ [key]: value });
  };

  const onDateTime = (which: "start" | "end", v: { date: string; time: string }) => {
    const patch: Partial<FormValues> =
      which === "start"
        ? { startDate: v.date, inspectionTime: v.time }
        : { endDate: v.date, inspectionEndTime: v.time };
    setValues((prev) => ({ ...prev, ...patch }));
    sendIfActive(patch);
  };

  const onFile = (file: File) => {
    const label = options?.document_types.find((d) => d.value === docType)?.label ?? "document";
    void session.upload(docType, label, file, docComment).then(() => setDocComment(""));
  };

  const bindRef = (key: string) => (el: HTMLElement | null) => {
    fieldRefs.current[key] = el;
  };

  const textInput = (key: TextKey, placeholder: string, multiline = false) => {
    const common = {
      ref: bindRef(key),
      value: (values[key] as string | undefined) ?? "",
      disabled: !editable,
      placeholder,
      onFocus: () => {
        focused.current = key;
      },
      onBlur: () => onBlurText(key),
      onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
        setValues((v) => ({ ...v, [key]: e.target.value })),
    };
    const box = fieldBox({ disabled: !editable, highlight: activeField === key, missing: missing.has(key) });
    return multiline ? (
      <textarea rows={5} {...common} className={`${box} py-3 resize-y placeholder:text-[#8a8a94]`} />
    ) : (
      <input {...common} className={`${box} placeholder:text-[#8a8a94]`} />
    );
  };

  const readOnlyInput = (value: string | number | undefined, placeholder: string) => (
    <input
      className={`${fieldBox({})} placeholder:text-[#8a8a94]`}
      value={value ?? ""}
      placeholder={placeholder}
      readOnly
      tabIndex={-1}
    />
  );

  const leasePicked = Boolean(companyId !== null && leaseCode);
  const canUpload = editable && leasePicked && agentActive && !busy;

  if (!isTenant && !formState) {
    return (
      <div className="h-full rounded-2xl bg-[#f4f5f7] flex items-center justify-center text-center p-10">
        <div>
          <div className="text-lg font-semibold text-[#1d1640] mb-1">No request open</div>
          <p className="text-sm text-slate-500">
            Open one from the notification bell or “My requests”. It appears here and in the chat.
          </p>
        </div>
      </div>
    );
  }

  const companyOptions = companies.map((c) => ({ value: String(c.tenant_profile_id), label: c.company_name }));
  // A reopened SR may belong to a company or lease outside this user's list.
  if (companyId !== null && !companyOptions.some((o) => o.value === String(companyId))) {
    companyOptions.push({ value: String(companyId), label: values.company_name ?? `Company ${companyId}` });
  }
  const leaseOptions = leases.map((l) => ({ value: l.lease_code, label: l.lease_brand_mall }));
  if (leaseCode && !leaseOptions.some((o) => o.value === leaseCode)) {
    leaseOptions.push({ value: leaseCode, label: values.lease_brand_mall ?? leaseCode });
  }

  return (
    <div className="h-full overflow-y-auto rounded-2xl bg-[#f4f5f7] text-slate-800">
      <div className="px-8 pt-5 text-[15px] text-[#5a5a66]">
        Dashboard <span className="mx-3">›</span> Service Requests <span className="mx-3">›</span>
        <span className="text-[#4B2A7B] font-semibold">New Service Requests</span>
      </div>

      {locked && formState?.banner && (
        <div className="mx-8 mt-4 border border-indigo-200 bg-indigo-50 px-4 py-3 text-sm text-indigo-900">
          {formState.banner}. Review and approval happen in the assistant.
          {formState.sr_id && <span className="ml-2 text-indigo-500">SR {formState.sr_id.slice(0, 8)}…</span>}
        </div>
      )}
      {loadError && (
        <div className="mx-8 mt-4 border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">{loadError}</div>
      )}

      {/* New Service Request — category card */}
      <section className="mx-8 mt-6 bg-white shadow-sm">
        <h1 className="px-7 py-6 text-[26px] font-semibold text-[#1d1640] border-b border-[#ececf0]">
          New Service Request
        </h1>
        <div className="px-10 py-7 grid grid-cols-1 md:grid-cols-2 gap-x-9 gap-y-6">
          <div>
            <FieldLabel text="Service Category" />
            <select className={`${fieldBox({ disabled: true })} appearance-none`} value={SERVICE_CATEGORY.value} disabled>
              <option value={SERVICE_CATEGORY.value}>{SERVICE_CATEGORY.label}</option>
            </select>
          </div>
          <div ref={bindRef("sub_category")}>
            <FieldLabel text="Sub Category" />
            <select
              className={`${fieldBox({ disabled: !editable || Boolean(formState?.sub_category) })} border-[#1d1640]`}
              value={subCategory}
              disabled={!editable || Boolean(formState?.sub_category)}
              onChange={(e) => onSubCategory(e.target.value)}
            >
              <option value="">Select sub category</option>
              {subOptions.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      </section>

      {/* Inspection form card */}
      <section className="mx-8 my-8 bg-white shadow-sm">
        <h2 className="px-7 py-6 text-[26px] font-semibold text-[#1d1640] border-b border-[#ececf0] flex items-center justify-between">
          <span>{subLabel || "Ceiling Inspection"}</span>
          {agentActive && (
            <span className="inline-flex items-center gap-2 text-sm font-normal text-[#8a6d1f]">
              <span className="w-2 h-2 rounded-full bg-[#C9A84C] animate-pulse" /> Assistant is filling this form
            </span>
          )}
        </h2>

        <div className="px-10 py-7 space-y-7">
          <p className="text-[17px] text-[#b3261e]">*Indicates required field</p>

          {editable && !agentActive && leaseCode && !subCategory && (
            <p className="text-sm text-[#8a6d1f] -mt-4">Choose the Sub Category to start the assistant.</p>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-16 gap-y-7">
            <div>
              <FieldLabel text="Company Name" required />
              <SearchSelect
                ref={bindRef("company_name")}
                value={companyId === null ? "" : String(companyId)}
                options={companyOptions}
                placeholder="Select Company Name"
                disabled={!editable}
                onChange={(v) => onCompany(v ? Number(v) : null)}
              />
            </div>
            <div>
              <FieldLabel text="Lease ID" required />
              <SearchSelect
                ref={(el) => {
                  fieldRefs.current.lease_code = el;
                  fieldRefs.current.lease_brand_mall = el;
                }}
                value={leaseCode}
                options={leaseOptions}
                placeholder="Lease Brand Mall"
                disabled={!editable || companyId === null}
                highlight={activeField === "lease_code" || activeField === "lease_brand_mall"}
                onChange={onLease}
              />
            </div>
            <div ref={bindRef("unit_codes")}>
              <FieldLabel text="Unit Code" required />
              <UnitCodeChips codes={values.unit_codes ?? []} />
            </div>
            <div>
              <FieldLabel text="Location" />
              {readOnlyInput(values.city, "Enter the location")}
            </div>
            <div>
              <FieldLabel text="Area (Sq.m.)" />
              {readOnlyInput(values.contracted_area, "Enter the area")}
            </div>
          </div>

          <div>
            <FieldLabel text="Title" required />
            {textInput("title", "Enter the subject/title")}
          </div>

          <div>
            <FieldLabel text="Description" required />
            {textInput("description", "", true)}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-x-16 gap-y-7 pt-6">
            <div>
              <FieldLabel text="Start Date" required />
              <DateTimeField
                ref={bindRef("startDate")}
                date={values.startDate}
                time={values.inspectionTime}
                disabled={!editable}
                highlight={activeField === "startDate"}
                missing={missing.has("startDate")}
                onCommit={(v) => onDateTime("start", v)}
              />
            </div>
            <div>
              <FieldLabel text="End Date" required />
              <DateTimeField
                ref={bindRef("endDate")}
                date={values.endDate}
                time={values.inspectionEndTime ?? values.inspectionTime}
                disabled={!editable}
                highlight={activeField === "endDate"}
                missing={missing.has("endDate")}
                onCommit={(v) => onDateTime("end", v)}
              />
            </div>
            <div>
              <FieldLabel text="Inspection conducted by" />
              <div className="min-h-[56px] px-7 flex items-center bg-[#f3f3f5] text-[17px] text-[#3b3b45]">
                {options?.inspection_done_by.label ?? "RDD Project Manager"}
              </div>
            </div>
          </div>

          <div>
            <FieldLabel text="Notes" />
            {textInput("notes", "", true)}
          </div>

          <hr className="border-[#d9d9de] !mt-16" />

          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-16 gap-y-7">
            <div>
              <FieldLabel text="Document Type" />
              <SearchSelect
                value={docType}
                options={(options?.document_types ?? []).map((d) => ({ value: d.value, label: d.label }))}
                placeholder="Select document type"
                disabled={!editable}
                onChange={(v) => v && setDocType(v)}
              />
            </div>
            <div className="hidden md:block" />
            <div>
              <FieldLabel text="Document" />
              <DocumentDrop canUpload={canUpload} leasePicked={leasePicked} onFile={onFile} />
              {(formState?.documents ?? []).map((d) => (
                <div key={d.document_id ?? d.file_name} className="mt-2 text-sm text-emerald-700">
                  ✓ {d.file_name} <span className="text-slate-400">({d.document_type})</span>
                </div>
              ))}
            </div>
            <div>
              <FieldLabel text="Comment" />
              <textarea
                rows={6}
                className={`${fieldBox({ disabled: !editable })} py-3 resize-y`}
                value={docComment}
                disabled={!editable}
                onChange={(e) => setDocComment(e.target.value)}
              />
            </div>
          </div>

          {/* The platform form's own `comments` field (formComments, required) sits below the attachments. */}
          <div>
            <FieldLabel text="Comments" required />
            {textInput("comments", "", true)}
          </div>
        </div>
      </section>
    </div>
  );
}

function DocumentDrop({
  canUpload,
  leasePicked,
  onFile,
}: {
  canUpload: boolean;
  leasePicked: boolean;
  onFile: (file: File) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={input}
        type="file"
        accept="application/pdf,image/jpeg,image/png"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onFile(file);
          e.target.value = "";
        }}
      />
      <button
        type="button"
        disabled={!canUpload}
        onClick={() => input.current?.click()}
        className={`w-full min-h-[150px] border-2 border-dashed px-8 py-6 text-left text-[17px] leading-relaxed ${
          canUpload
            ? "border-[#4B2A7B]/50 bg-white text-[#5a5a66] hover:bg-[#faf8fd]"
            : "border-[#d6336c] bg-[#f3f3f5] text-[#9a9aa6] cursor-not-allowed"
        }`}
      >
        {leasePicked
          ? canUpload
            ? "Drag a file here or click to upload (PDF, JPEG or PNG)."
            : "Start the request with the assistant before uploading."
          : "Please select company name and lease brand mall before proceeding with the upload of attachments."}
      </button>
    </>
  );
}
