"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AlertCircle, CalendarClock, CheckCircle2, RefreshCw, Send, UploadCloud, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const ASPECT_RATIOS = [
  { value: "SQUARE_1_1", label: "1:1" },
  { value: "PORTRAIT_4_5", label: "4:5" },
  { value: "LANDSCAPE_16_9", label: "16:9" }
];

const SCHEDULER_PLATFORMS = ["INSTAGRAM", "FACEBOOK", "TWITTER"] as const;

type AssetPreview = {
  id: string;
  file_name: string;
  content_type: string;
  preview_url: string | null;
};

type Tag = {
  id: string;
  display_name: string;
  handle: string | null;
  platform: string | null;
  notes?: string | null;
};

type Post = {
  id: string;
  platform: string;
  generated_caption: string;
  selected_aspect_ratio: string;
  approval_status: string;
  publish_status: string;
  scheduled_publish_time: string | null;
  published_at: string | null;
  rejection_reason: string | null;
  last_publish_error: string | null;
  asset: AssetPreview | null;
  tags: Tag[];
};

type DraftState = {
  caption: string;
  aspectRatio: string;
  tagIds: string[];
  rejectionReason: string;
};

type ScheduleState = {
  scheduledAt: string;
  platforms: string[];
};

export default function TeamWorkspace() {
  const [pendingPosts, setPendingPosts] = useState<Post[]>([]);
  const [approvedPosts, setApprovedPosts] = useState<Post[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [drafts, setDrafts] = useState<Record<string, DraftState>>({});
  const [schedules, setSchedules] = useState<Record<string, ScheduleState>>({});
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function loadWorkspace() {
    setLoading(true);
    setError(null);
    try {
      const [pending, approved, directory] = await Promise.all([
        api<Post[]>("/posts?approval_status=PENDING"),
        api<Post[]>("/posts?approval_status=APPROVED"),
        api<Tag[]>("/tags")
      ]);
      setPendingPosts(pending);
      setApprovedPosts(approved);
      setTags(directory);
      setDrafts((current) => ({
        ...current,
        ...Object.fromEntries(
          pending.map((post) => [
            post.id,
            current[post.id] ?? {
              caption: post.generated_caption,
              aspectRatio: normalizeAspect(post.selected_aspect_ratio),
              tagIds: post.tags.map((tag) => tag.id),
              rejectionReason: ""
            }
          ])
        )
      }));
      setSchedules((current) => ({
        ...current,
        ...Object.fromEntries(
          approved.map((post) => [
            post.id,
            current[post.id] ?? {
              scheduledAt: "",
              platforms: SCHEDULER_PLATFORMS.includes(post.platform as (typeof SCHEDULER_PLATFORMS)[number])
                ? [post.platform]
                : ["FACEBOOK"]
            }
          ])
        )
      }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load workspace.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadWorkspace();
  }, []);

  const queuedPosts = useMemo(
    () => approvedPosts.filter((post) => post.publish_status !== "NOT_SCHEDULED"),
    [approvedPosts]
  );

  async function approve(post: Post) {
    const draft = drafts[post.id];
    setBusyId(post.id);
    setError(null);
    try {
      await api(`/posts/${post.id}/approve`, {
        method: "PATCH",
        body: JSON.stringify({
          generated_caption: draft.caption,
          selected_aspect_ratio: draft.aspectRatio,
          tag_ids: draft.tagIds
        })
      });
      await loadWorkspace();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Approval failed.");
    } finally {
      setBusyId(null);
    }
  }

  async function reject(post: Post) {
    const reason = drafts[post.id]?.rejectionReason || "Rejected during human review.";
    setBusyId(post.id);
    setError(null);
    try {
      await api(`/posts/${post.id}/reject`, {
        method: "PATCH",
        body: JSON.stringify({ rejection_reason: reason })
      });
      await loadWorkspace();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Rejection failed.");
    } finally {
      setBusyId(null);
    }
  }

  async function schedule(post: Post) {
    const state = schedules[post.id];
    if (!state?.scheduledAt || state.platforms.length === 0) {
      setError("Choose a publish time and at least one platform.");
      return;
    }
    setBusyId(post.id);
    setError(null);
    try {
      await api(`/posts/${post.id}/schedule`, {
        method: "PATCH",
        body: JSON.stringify({
          scheduled_publish_time: new Date(state.scheduledAt).toISOString(),
          platforms: state.platforms
        })
      });
      await loadWorkspace();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Scheduling failed.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <main className="min-h-screen bg-[#f7f8f3] text-[#171717]">
      <section className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-4 border-b border-[#d8ddd2] pb-6 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-sm font-semibold uppercase tracking-wide text-[#0f7b63]">Team workspace</p>
            <h1 className="mt-2 text-3xl font-semibold tracking-normal sm:text-4xl">Approve, schedule, publish.</h1>
            <p className="mt-3 max-w-2xl text-base leading-7 text-[#53605a]">
              Review AI drafts, tune the caption and tags, then queue approved posts for the channels that matter.
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Button asChild>
              <Link href="/upload-generate">
                <UploadCloud className="mr-2 h-4 w-4" />
                Upload & Generate
              </Link>
            </Button>
            <Button onClick={loadWorkspace} disabled={loading} variant="outline">
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh
            </Button>
          </div>
        </header>

        {error ? (
          <div className="mt-5 flex items-start gap-3 rounded-lg border border-[#e3b1a7] bg-[#fff7f5] p-4 text-sm text-[#8a2d1f]">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <p>{error}</p>
          </div>
        ) : null}

        <section className="mt-8">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-2xl font-semibold tracking-normal">Human approval</h2>
              <p className="mt-1 text-sm text-[#53605a]">{pendingPosts.length} pending draft posts</p>
            </div>
          </div>

          {loading ? <LoadingGrid /> : null}

          {!loading && pendingPosts.length === 0 ? (
            <EmptyState title="No pending drafts" body="Approved and rejected posts will move out of this queue." />
          ) : null}

          <div className="mt-5 grid gap-5 lg:grid-cols-2">
            {pendingPosts.map((post) => {
              const draft = drafts[post.id];
              return (
                <article key={post.id} className="grid overflow-hidden rounded-lg border border-[#d8ddd2] bg-white lg:grid-cols-[minmax(220px,0.85fr)_1.15fr]">
                  <MockupImage post={post} />
                  <div className="p-5">
                    <div className="flex flex-wrap items-center gap-2">
                      <PlatformBadge platform={post.platform} />
                      <span className="rounded-md bg-[#edf7f3] px-2 py-1 text-xs font-medium text-[#0f7b63]">Pending</span>
                    </div>

                    <label className="mt-5 block text-sm font-semibold" htmlFor={`caption-${post.id}`}>
                      Caption
                    </label>
                    <textarea
                      id={`caption-${post.id}`}
                      className="mt-2 min-h-36 w-full resize-y rounded-md border border-[#cbd3c7] bg-white p-3 text-sm leading-6 outline-none focus:border-[#0f7b63] focus:ring-2 focus:ring-[#b9e6d8]"
                      value={draft?.caption ?? post.generated_caption}
                      onChange={(event) => updateDraft(post.id, { caption: event.target.value })}
                    />

                    <div className="mt-5 grid gap-4 sm:grid-cols-2">
                      <div>
                        <p className="text-sm font-semibold">Aspect ratio</p>
                        <div className="mt-2 flex flex-wrap gap-2">
                          {ASPECT_RATIOS.map((ratio) => (
                            <button
                              key={ratio.value}
                              type="button"
                              className={`rounded-md border px-3 py-2 text-sm ${
                                draft?.aspectRatio === ratio.value
                                  ? "border-[#0f7b63] bg-[#e6f5ef] text-[#0d5f4d]"
                                  : "border-[#cbd3c7] bg-white text-[#3d4742]"
                              }`}
                              onClick={() => updateDraft(post.id, { aspectRatio: ratio.value })}
                            >
                              {ratio.label}
                            </button>
                          ))}
                        </div>
                      </div>

                      <div>
                        <p className="text-sm font-semibold">People tags</p>
                        <div className="mt-2 max-h-28 space-y-2 overflow-auto rounded-md border border-[#d8ddd2] p-2">
                          {tags.length === 0 ? <p className="text-sm text-[#53605a]">No active tags yet.</p> : null}
                          {tags.map((tag) => (
                            <label key={tag.id} className="flex items-center gap-2 text-sm">
                              <input
                                type="checkbox"
                                checked={draft?.tagIds.includes(tag.id) ?? false}
                                onChange={() => toggleTag(post.id, tag.id)}
                              />
                              <span>{tag.display_name}</span>
                              {tag.handle ? <span className="text-[#66736d]">{tag.handle}</span> : null}
                            </label>
                          ))}
                        </div>
                      </div>
                    </div>

                    <label className="mt-5 block text-sm font-semibold" htmlFor={`reject-${post.id}`}>
                      Rejection note
                    </label>
                    <input
                      id={`reject-${post.id}`}
                      className="mt-2 w-full rounded-md border border-[#cbd3c7] px-3 py-2 text-sm outline-none focus:border-[#0f7b63] focus:ring-2 focus:ring-[#b9e6d8]"
                      placeholder="Optional note for the generator"
                      value={draft?.rejectionReason ?? ""}
                      onChange={(event) => updateDraft(post.id, { rejectionReason: event.target.value })}
                    />

                    <div className="mt-5 flex flex-wrap gap-3">
                      <Button onClick={() => approve(post)} disabled={busyId === post.id}>
                        <CheckCircle2 className="mr-2 h-4 w-4" />
                        Approve
                      </Button>
                      <Button variant="outline" onClick={() => reject(post)} disabled={busyId === post.id}>
                        <XCircle className="mr-2 h-4 w-4" />
                        Reject
                      </Button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        </section>

        <section className="mt-12 pb-12">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 className="text-2xl font-semibold tracking-normal">Publishing scheduler</h2>
              <p className="mt-1 text-sm text-[#53605a]">Queue approved posts for Instagram, Facebook, and Twitter.</p>
            </div>
            <p className="text-sm text-[#53605a]">{queuedPosts.length} queued or processed</p>
          </div>

          {!loading && approvedPosts.length === 0 ? (
            <EmptyState title="No approved posts" body="Approved drafts will appear here for scheduling." />
          ) : null}

          <div className="mt-5 grid gap-4">
            {approvedPosts.map((post) => {
              const state = schedules[post.id] ?? { scheduledAt: "", platforms: ["FACEBOOK"] };
              return (
                <article key={post.id} className="grid gap-4 rounded-lg border border-[#d8ddd2] bg-white p-4 md:grid-cols-[140px_1fr]">
                  <MockupImage post={post} compact />
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <PlatformBadge platform={post.platform} />
                      <span className="rounded-md bg-[#eef1ed] px-2 py-1 text-xs font-medium text-[#4a554f]">
                        {post.publish_status.replace("_", " ")}
                      </span>
                      {post.scheduled_publish_time ? (
                        <span className="text-xs text-[#53605a]">
                          {new Date(post.scheduled_publish_time).toLocaleString()}
                        </span>
                      ) : null}
                    </div>
                    <p className="mt-3 line-clamp-3 text-sm leading-6 text-[#303832]">{post.generated_caption}</p>
                    {post.last_publish_error ? (
                      <p className="mt-2 text-sm text-[#9b3325]">{post.last_publish_error}</p>
                    ) : null}

                    <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(220px,280px)_1fr_auto] lg:items-end">
                      <label className="block text-sm font-semibold">
                        Publish time
                        <input
                          type="datetime-local"
                          className="mt-2 w-full rounded-md border border-[#cbd3c7] px-3 py-2 text-sm outline-none focus:border-[#0f7b63] focus:ring-2 focus:ring-[#b9e6d8]"
                          value={state.scheduledAt}
                          onChange={(event) => updateSchedule(post.id, { scheduledAt: event.target.value })}
                        />
                      </label>

                      <div>
                        <p className="text-sm font-semibold">Channels</p>
                        <div className="mt-2 flex flex-wrap gap-2">
                          {SCHEDULER_PLATFORMS.map((platform) => (
                            <button
                              key={platform}
                              type="button"
                              className={`rounded-md border px-3 py-2 text-sm ${
                                state.platforms.includes(platform)
                                  ? "border-[#0f7b63] bg-[#e6f5ef] text-[#0d5f4d]"
                                  : "border-[#cbd3c7] bg-white text-[#3d4742]"
                              }`}
                              onClick={() => toggleSchedulePlatform(post.id, platform)}
                            >
                              {titleCase(platform)}
                            </button>
                          ))}
                        </div>
                      </div>

                      <Button onClick={() => schedule(post)} disabled={busyId === post.id}>
                        <Send className="mr-2 h-4 w-4" />
                        Schedule
                      </Button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      </section>
    </main>
  );

  function updateDraft(postId: string, patch: Partial<DraftState>) {
    setDrafts((current) => ({ ...current, [postId]: { ...defaultDraft(), ...current[postId], ...patch } }));
  }

  function toggleTag(postId: string, tagId: string) {
    setDrafts((current) => {
      const draft = current[postId] ?? defaultDraft();
      const tagIds = draft.tagIds.includes(tagId) ? draft.tagIds.filter((id) => id !== tagId) : [...draft.tagIds, tagId];
      return { ...current, [postId]: { ...draft, tagIds } };
    });
  }

  function updateSchedule(postId: string, patch: Partial<ScheduleState>) {
    setSchedules((current) => ({ ...current, [postId]: { ...defaultSchedule(), ...current[postId], ...patch } }));
  }

  function toggleSchedulePlatform(postId: string, platform: string) {
    setSchedules((current) => {
      const state = current[postId] ?? { scheduledAt: "", platforms: [] };
      const platforms = state.platforms.includes(platform)
        ? state.platforms.filter((item) => item !== platform)
        : [...state.platforms, platform];
      return { ...current, [postId]: { ...state, platforms } };
    });
  }
}

function defaultDraft(): DraftState {
  return { caption: "", aspectRatio: "SQUARE_1_1", tagIds: [], rejectionReason: "" };
}

function defaultSchedule(): ScheduleState {
  return { scheduledAt: "", platforms: ["FACEBOOK"] };
}

function MockupImage({ post, compact = false }: { post: Post; compact?: boolean }) {
  return (
    <div className={`relative overflow-hidden bg-[#e9ede7] ${compact ? "h-32 rounded-md" : "min-h-72 lg:min-h-full"}`}>
      {post.asset?.preview_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={post.asset.preview_url} alt="" className="h-full w-full object-cover" />
      ) : (
        <div className="flex h-full min-h-32 items-center justify-center p-6 text-center text-sm text-[#53605a]">
          Generated image mockup will appear here.
        </div>
      )}
      <div className="absolute bottom-3 left-3 rounded-md bg-white/90 px-2 py-1 text-xs font-medium text-[#303832]">
        {post.asset?.file_name ?? "No asset"}
      </div>
    </div>
  );
}

function PlatformBadge({ platform }: { platform: string }) {
  return <span className="rounded-md bg-[#171717] px-2 py-1 text-xs font-semibold text-white">{titleCase(platform)}</span>;
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="mt-5 rounded-lg border border-dashed border-[#c5cec1] bg-white p-8 text-center">
      <CalendarClock className="mx-auto h-8 w-8 text-[#0f7b63]" />
      <h3 className="mt-3 font-semibold">{title}</h3>
      <p className="mt-1 text-sm text-[#53605a]">{body}</p>
    </div>
  );
}

function LoadingGrid() {
  return (
    <div className="mt-5 grid gap-5 lg:grid-cols-2">
      {[1, 2].map((item) => (
        <div key={item} className="grid overflow-hidden rounded-lg border border-[#d8ddd2] bg-white lg:grid-cols-[minmax(220px,0.85fr)_1.15fr]">
          <div className="min-h-72 animate-pulse bg-[#e9ede7]" />
          <div className="space-y-4 p-5">
            <div className="h-5 w-32 animate-pulse rounded bg-[#e9ede7]" />
            <div className="h-36 animate-pulse rounded bg-[#e9ede7]" />
            <div className="h-10 animate-pulse rounded bg-[#e9ede7]" />
          </div>
        </div>
      ))}
    </div>
  );
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    }
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(typeof payload?.detail === "string" ? payload.detail : "Request failed.");
  }
  return response.json();
}

function titleCase(value: string) {
  return value.toLowerCase().replace(/(^|_)([a-z])/g, (_match, prefix, char) => `${prefix ? " " : ""}${char.toUpperCase()}`);
}

function normalizeAspect(value: string) {
  return ASPECT_RATIOS.some((ratio) => ratio.value === value) ? value : "SQUARE_1_1";
}
