"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { AlertCircle, ArrowLeft, Loader2, UploadCloud } from "lucide-react";
import { Button } from "@/components/ui/button";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function UploadGeneratePage() {
  const router = useRouter();
  const [campaignId, setCampaignId] = useState("");
  const [description, setDescription] = useState("");
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [modelImage, setModelImage] = useState<File | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    if (!campaignId.trim()) {
      setError("Campaign ID is required.");
      return;
    }
    if (!pdfFile) {
      setError("Choose a magazine PDF.");
      return;
    }
    if (!modelImage) {
      setError("Choose a model image.");
      return;
    }

    const formData = new FormData();
    formData.append("campaign_id", campaignId.trim());
    formData.append("description", description.trim());
    formData.append("file", pdfFile);
    formData.append("model_image", modelImage);

    setIsSubmitting(true);
    try {
      const response = await fetch(`${API_BASE_URL}/ai/pipeline/uploads/draft-posts`, {
        method: "POST",
        body: formData
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(formatApiError(payload?.detail) ?? "Upload and generation failed.");
      }

      router.push("/");
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Upload and generation failed.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-[#f7f8f3] text-[#171717]">
      <section className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center px-4 py-8 sm:px-6">
        <Link href="/" className="mb-6 inline-flex w-fit items-center text-sm font-medium text-[#0f7b63]">
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to approval dashboard
        </Link>

        <div className="rounded-lg border border-[#d8ddd2] bg-white p-5 shadow-sm sm:p-7">
          <div className="flex items-start gap-4">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-[#e6f5ef] text-[#0f7b63]">
              <UploadCloud className="h-5 w-5" />
            </div>
            <div>
              <p className="text-sm font-semibold uppercase tracking-wide text-[#0f7b63]">Upload & Generate</p>
              <h1 className="mt-2 text-3xl font-semibold tracking-normal">Create pending drafts</h1>
              <p className="mt-3 text-sm leading-6 text-[#53605a]">
                Upload a magazine PDF and model image to generate review-ready social drafts.
              </p>
            </div>
          </div>

          {error ? (
            <div className="mt-6 flex items-start gap-3 rounded-lg border border-[#e3b1a7] bg-[#fff7f5] p-4 text-sm text-[#8a2d1f]">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <p>{error}</p>
            </div>
          ) : null}

          <form className="mt-7 space-y-5" onSubmit={submit}>
            <label className="block text-sm font-semibold" htmlFor="campaign-id">
              Campaign ID
              <input
                id="campaign-id"
                className="mt-2 w-full rounded-md border border-[#cbd3c7] px-3 py-2 text-sm outline-none focus:border-[#0f7b63] focus:ring-2 focus:ring-[#b9e6d8]"
                placeholder="Paste the campaign UUID"
                value={campaignId}
                onChange={(event) => setCampaignId(event.target.value)}
                disabled={isSubmitting}
              />
            </label>

            <label className="block text-sm font-semibold" htmlFor="description">
              Description
              <input
                id="description"
                className="mt-2 w-full rounded-md border border-[#cbd3c7] px-3 py-2 text-sm outline-none focus:border-[#0f7b63] focus:ring-2 focus:ring-[#b9e6d8]"
                placeholder="Optional campaign note"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                disabled={isSubmitting}
              />
            </label>

            <div className="grid gap-4 sm:grid-cols-2">
              <FileInput
                id="magazine-pdf"
                label="Magazine PDF"
                accept="application/pdf"
                file={pdfFile}
                disabled={isSubmitting}
                onChange={setPdfFile}
              />
              <FileInput
                id="model-image"
                label="Model image"
                accept="image/png,image/jpeg,image/webp"
                file={modelImage}
                disabled={isSubmitting}
                onChange={setModelImage}
              />
            </div>

            {isSubmitting ? (
              <div className="rounded-lg border border-[#cbd3c7] bg-[#f2f5ef] p-4 text-sm leading-6 text-[#3d4742]">
                <div className="flex items-center font-semibold">
                  <Loader2 className="mr-2 h-4 w-4 animate-spin text-[#0f7b63]" />
                  Generating drafts
                </div>
                <p className="mt-2">
                  The backend is parsing the PDF, running the AI copywriter, reviewing brand safety, and executing the
                  template-based image composition.
                </p>
              </div>
            ) : null}

            <div className="flex flex-wrap gap-3 pt-2">
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <UploadCloud className="mr-2 h-4 w-4" />}
                Upload & Generate
              </Button>
              <Button asChild variant="outline" disabled={isSubmitting}>
                <Link href="/">Cancel</Link>
              </Button>
            </div>
          </form>
        </div>
      </section>
    </main>
  );
}

function FileInput({
  id,
  label,
  accept,
  file,
  disabled,
  onChange
}: {
  id: string;
  label: string;
  accept: string;
  file: File | null;
  disabled: boolean;
  onChange: (file: File | null) => void;
}) {
  return (
    <label className="block text-sm font-semibold" htmlFor={id}>
      {label}
      <input
        id={id}
        type="file"
        accept={accept}
        className="mt-2 w-full rounded-md border border-[#cbd3c7] bg-white px-3 py-2 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-[#171717] file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-white disabled:opacity-60"
        disabled={disabled}
        onChange={(event) => onChange(event.target.files?.[0] ?? null)}
      />
      <span className="mt-2 block min-h-5 text-xs font-normal text-[#53605a]">{file ? file.name : "No file selected"}</span>
    </label>
  );
}

function formatApiError(detail: unknown) {
  if (!detail) {
    return null;
  }
  if (typeof detail === "string") {
    return detail;
  }
  if (typeof detail === "object") {
    return JSON.stringify(detail);
  }
  return null;
}
