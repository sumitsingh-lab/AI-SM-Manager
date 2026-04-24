"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  ArrowUpRight,
  BadgeCheck,
  ChevronDown,
  Clock3,
  Loader2,
  LayoutGrid,
  RefreshCw,
  Sparkles,
  Tag,
  UploadCloud,
  XCircle
} from "lucide-react";
import { Button } from "@/components/ui/button";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const ASPECT_OPTIONS = [
  { value: "SQUARE_1_1", label: "1:1" },
  { value: "PORTRAIT_4_5", label: "4:5" },
  { value: "LANDSCAPE_16_9", label: "16:9" }
] as const;

const SCHEDULER_PLATFORMS = ["INSTAGRAM", "FACEBOOK", "TWITTER"] as const;

type AssetPreview = {
  id: string;
  file_name: string;
  content_type: string;
  preview_url: string | null;
};

type TagDirectoryItem = {
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
  tags: TagDirectoryItem[];
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

type BrandCropResponse = {
  asset_id: string;
  local_url: string;
};

type PublishApprovalRequest = {
  generated_caption: string;
  selected_aspect_ratio: string;
  tag_ids: string[];
  asset_id: string;
};

export default function DashboardPage() {
  const [pendingPosts, setPendingPosts] = useState<Post[]>([]);
  const [approvedPosts, setApprovedPosts] = useState<Post[]>([]);
  const [tags, setTags] = useState<TagDirectoryItem[]>([]);
  const [drafts, setDrafts] = useState<Record<string, DraftState>>({});
  const [schedules, setSchedules] = useState<Record<string, ScheduleState>>({});
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [openTagMenuId, setOpenTagMenuId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function loadWorkspace() {
    setLoading(true);
    setError(null);
    try {
      const [pending, approved, directory] = await Promise.all([
        api<Post[]>("/posts?approval_status=PENDING"),
        api<Post[]>("/posts?approval_status=APPROVED"),
        api<TagDirectoryItem[]>("/tags")
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

  const stats = useMemo(
    () => [
      { label: "Pending", value: pendingPosts.length },
      { label: "Approved", value: approvedPosts.length },
      { label: "Tags", value: tags.length }
    ],
    [approvedPosts.length, pendingPosts.length, tags.length]
  );

  async function approve(post: Post) {
    const draft = drafts[post.id];
    if (!draft) {
      setError("Draft controls are not ready yet.");
      return;
    }
    if (!post.asset?.id) {
      setError("This post is missing a source image asset.");
      return;
    }

    setBusyId(post.id);
    setError(null);
    try {
      const branded = await api<BrandCropResponse>("/images/brand-and-crop", {
        method: "POST",
        body: JSON.stringify({
          source_asset_id: post.asset.id,
          aspect_ratio: draft.aspectRatio
        })
      });

      const updated = await api<Post>(`/posts/${post.id}/approve`, {
        method: "PATCH",
        body: JSON.stringify({
          generated_caption: draft.caption,
          selected_aspect_ratio: draft.aspectRatio,
          tag_ids: draft.tagIds,
          asset_id: branded.asset_id
        } satisfies PublishApprovalRequest)
      });

      setPendingPosts((current) => current.filter((item) => item.id !== post.id));
      setApprovedPosts((current) => [updated, ...current.filter((item) => item.id !== post.id)]);
      setDrafts((current) => {
        const next = { ...current };
        delete next[post.id];
        return next;
      });
      setSchedules((current) => ({
        ...current,
        [updated.id]: current[updated.id] ?? { scheduledAt: "", platforms: [updated.platform] }
      }));
      setOpenTagMenuId(null);
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
    <main className="min-h-screen bg-[#f4f2ec] text-[#111111]">
      <div className="grid min-h-screen lg:grid-cols-[280px_1fr]">
        <aside className="border-b border-[#d8d1c6] bg-[#111111] px-5 py-6 text-white lg:border-b-0 lg:border-r lg:border-[#2a2a2a] lg:px-6">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-[#e8f3ee] text-[#0b5d4a]">
              <Sparkles className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.28em] text-[#9ccbbd]">AI-SM Manager</p>
              <h1 className="mt-1 text-xl font-semibold">Production Dashboard</h1>
            </div>
          </div>

          <nav className="mt-8 space-y-2">
            <NavLink active label="Grid Review" icon={<LayoutGrid className="h-4 w-4" />} />
            <NavLink label="Upload & Generate" href="/upload-generate" icon={<UploadCloud className="h-4 w-4" />} />
          </nav>

          <div className="mt-8 rounded-2xl border border-white/10 bg-white/5 p-4">
            <p className="text-sm font-medium text-white/80">Workspace summary</p>
            <div className="mt-4 space-y-3">
              {stats.map((stat) => (
                <div key={stat.label} className="flex items-center justify-between">
                  <span className="text-sm text-white/70">{stat.label}</span>
                  <span className="font-mono text-base text-white">{stat.value}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="mt-8 rounded-2xl border border-white/10 bg-gradient-to-br from-white/10 to-transparent p-4">
            <p className="text-sm font-semibold text-white">Workflow</p>
            <p className="mt-2 text-sm leading-6 text-white/70">
              Review pending cards in a 3-column grid, brand the selected image, then approve it into the approved
              queue with tags pre-populated from the backend.
            </p>
          </div>
        </aside>

        <section className="px-4 py-6 sm:px-6 lg:px-8">
          <header className="rounded-[2rem] border border-[#dbd4ca] bg-white/80 p-6 shadow-sm backdrop-blur">
            <div className="flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
              <div className="max-w-3xl">
                <p className="text-sm font-semibold uppercase tracking-[0.28em] text-[#0b5d4a]">Branding & approval</p>
                <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">
                  Review the grid, brand the image, and push the post into production.
                </h2>
                <p className="mt-3 text-base leading-7 text-[#56504a]">
                  Pending cards are editable, brand-aware, and ready to be converted into polished Instagram-ready
                  assets. Approved cards move over automatically once the branded crop is saved.
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
                  <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                  Refresh
                </Button>
              </div>
            </div>
          </header>

          {error ? (
            <div className="mt-5 flex items-start gap-3 rounded-2xl border border-[#e4b7aa] bg-[#fff7f3] p-4 text-sm text-[#8e3527]">
              <div className="mt-0.5 rounded-full bg-[#ffe8e1] p-1">
                <XCircle className="h-4 w-4" />
              </div>
              <p>{error}</p>
            </div>
          ) : null}

          <section className="mt-8">
            <SectionHeader
              title="Pending"
              body={`${pendingPosts.length} posts waiting for branded approval`}
              right={<BadgeChip>3-column Instagram grid</BadgeChip>}
            />

            {loading ? <LoadingGrid /> : null}

            {!loading && pendingPosts.length === 0 ? (
              <EmptyState
                title="No pending posts"
                body="New concepts will appear here once the pipeline finishes generating them."
              />
            ) : null}

            <div className="mt-5 grid grid-cols-1 gap-5 md:grid-cols-3">
              {pendingPosts.map((post) => {
                const draft = drafts[post.id] ?? defaultDraft(post);
                const isBusy = busyId === post.id;
                return (
                  <PostCard
                    key={post.id}
                    post={post}
                    busy={isBusy}
                    badge="Pending"
                    aspectRatio={draft.aspectRatio}
                    imageClassName={ratioToAspectClass(draft.aspectRatio)}
                    body={
                      <>
                        <label className="block text-sm font-semibold text-[#1b1b1b]" htmlFor={`caption-${post.id}`}>
                          Caption
                        </label>
                        <textarea
                          id={`caption-${post.id}`}
                          className="mt-2 min-h-40 w-full resize-y rounded-2xl border border-[#d8d1c6] bg-white p-3 text-sm leading-6 outline-none transition focus:border-[#0b5d4a] focus:ring-2 focus:ring-[#d4eee6]"
                          value={draft.caption}
                          onChange={(event) => updateDraft(post.id, { caption: event.target.value })}
                        />

                        <div className="mt-4 grid gap-3">
                          <label className="block text-sm font-semibold text-[#1b1b1b]" htmlFor={`aspect-${post.id}`}>
                            Aspect ratio
                          </label>
                          <select
                            id={`aspect-${post.id}`}
                            className="w-full rounded-2xl border border-[#d8d1c6] bg-white px-3 py-2 text-sm outline-none transition focus:border-[#0b5d4a] focus:ring-2 focus:ring-[#d4eee6]"
                            value={draft.aspectRatio}
                            onChange={(event) => updateDraft(post.id, { aspectRatio: event.target.value })}
                          >
                            {ASPECT_OPTIONS.map((option) => (
                              <option key={option.value} value={option.value}>
                                {option.label}
                              </option>
                            ))}
                          </select>
                        </div>

                        <div className="mt-4">
                          <TagMultiSelect
                            tags={tags}
                            selectedTagIds={draft.tagIds}
                            open={openTagMenuId === post.id}
                            onToggleOpen={() =>
                              setOpenTagMenuId((current) => (current === post.id ? null : post.id))
                            }
                            onToggleTag={(tagId) => toggleTag(post.id, tagId)}
                          />
                        </div>

                        <label className="mt-4 block text-sm font-semibold text-[#1b1b1b]" htmlFor={`reject-${post.id}`}>
                          Rejection note
                        </label>
                        <input
                          id={`reject-${post.id}`}
                          className="mt-2 w-full rounded-2xl border border-[#d8d1c6] px-3 py-2 text-sm outline-none transition focus:border-[#0b5d4a] focus:ring-2 focus:ring-[#d4eee6]"
                          placeholder="Optional note for the generator"
                          value={draft.rejectionReason}
                          onChange={(event) => updateDraft(post.id, { rejectionReason: event.target.value })}
                        /> 

                        <div className="mt-5 flex flex-wrap gap-3">
                          <Button onClick={() => approve(post)} disabled={isBusy}>
                            {isBusy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <BadgeCheck className="mr-2 h-4 w-4" />}
                            Approve
                          </Button>
                          <Button variant="outline" onClick={() => reject(post)} disabled={isBusy}>
                            <XCircle className="mr-2 h-4 w-4" />
                            Reject
                          </Button>
                        </div>
                      </>
                    }
                  />
                );
              })}
            </div>
          </section>

          <section className="mt-12 pb-12">
            <SectionHeader
              title="Approved"
              body={`${approvedPosts.length} posts ready for scheduling or publishing`}
              right={<BadgeChip>{approvedPosts.filter((post) => post.publish_status !== "NOT_SCHEDULED").length} queued</BadgeChip>}
            />

            {!loading && approvedPosts.length === 0 ? (
              <EmptyState title="No approved posts" body="Branded approvals will move into this queue automatically." />
            ) : null}

            <div className="mt-5 grid grid-cols-1 gap-5 md:grid-cols-3">
              {approvedPosts.map((post) => {
                const state = schedules[post.id] ?? { scheduledAt: "", platforms: ["FACEBOOK"] };
                const isBusy = busyId === post.id;
                return (
                  <PostCard
                    key={post.id}
                    post={post}
                    busy={isBusy}
                    badge={post.publish_status.replace("_", " ")}
                    aspectRatio={post.selected_aspect_ratio}
                    imageClassName={ratioToAspectClass(post.selected_aspect_ratio)}
                    body={
                      <>
                        <p className="text-sm leading-6 text-[#3a3732]">{post.generated_caption}</p>

                        <div className="mt-4 flex flex-wrap gap-2">
                          {post.tags.length === 0 ? <BadgeChip muted>No tags</BadgeChip> : null}
                          {post.tags.map((tag) => (
                            <BadgeChip key={tag.id}>
                              {tag.display_name}
                              {tag.handle ? <span className="ml-1 text-[#7a756f]">{tag.handle}</span> : null}
                            </BadgeChip>
                          ))}
                        </div>

                        <div className="mt-4 grid gap-3">
                          <label className="block text-sm font-semibold text-[#1b1b1b]">
                            Publish time
                            <input
                              type="datetime-local"
                              className="mt-2 w-full rounded-2xl border border-[#d8d1c6] px-3 py-2 text-sm outline-none transition focus:border-[#0b5d4a] focus:ring-2 focus:ring-[#d4eee6]"
                              value={state.scheduledAt}
                              onChange={(event) => updateSchedule(post.id, { scheduledAt: event.target.value })}
                            />
                          </label>

                          <div>
                            <p className="text-sm font-semibold text-[#1b1b1b]">Channels</p>
                            <div className="mt-2 flex flex-wrap gap-2">
                              {SCHEDULER_PLATFORMS.map((platform) => (
                                <button
                                  key={platform}
                                  type="button"
                                  className={`rounded-2xl border px-3 py-2 text-sm transition ${
                                    state.platforms.includes(platform)
                                      ? "border-[#0b5d4a] bg-[#e8f3ee] text-[#0b5d4a]"
                                      : "border-[#d8d1c6] bg-white text-[#3a3732]"
                                  }`}
                                  onClick={() => toggleSchedulePlatform(post.id, platform)}
                                >
                                  {titleCase(platform)}
                                </button>
                              ))}
                            </div>
                          </div>

                          <Button onClick={() => schedule(post)} disabled={isBusy}>
                            <Clock3 className="mr-2 h-4 w-4" />
                            Schedule
                          </Button>
                        </div>
                      </>
                    }
                  />
                );
              })}
            </div>
          </section>
        </section>
      </div>
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

function PostCard({
  post,
  busy,
  badge,
  aspectRatio,
  imageClassName,
  body
}: {
  post: Post;
  busy: boolean;
  badge: string;
  aspectRatio: string;
  imageClassName: string;
  body: ReactNode;
}) {
  return (
    <article className="overflow-visible rounded-[1.75rem] border border-[#d9d2c7] bg-white shadow-[0_14px_45px_rgba(17,17,17,0.06)]">
      <div className={`relative overflow-hidden bg-[#e9ece6] ${imageClassName}`}>
        {post.asset?.preview_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={post.asset.preview_url} alt="" className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full min-h-56 items-center justify-center p-6 text-center text-sm text-[#63615b]">
            Branded image will appear here.
          </div>
        )}

        <div className="absolute left-4 top-4 flex flex-wrap items-center gap-2">
          <BadgeChip dark>{titleCase(post.platform)}</BadgeChip>
          <BadgeChip>{badge}</BadgeChip>
        </div>

        <div className="absolute bottom-4 right-4 rounded-full bg-black/70 px-3 py-1 text-xs font-medium text-white">
          {aspectLabel(aspectRatio)}
        </div>
      </div>

      <div className="p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-[#0b5d4a]">
              {busy ? "Working..." : "Post concept"}
            </p>
            {post.last_publish_error ? (
              <p className="mt-2 text-sm text-[#8e3527]">{post.last_publish_error}</p>
            ) : null}
          </div>
          {post.scheduled_publish_time ? (
            <span className="rounded-full bg-[#eef1ed] px-3 py-1 text-xs font-medium text-[#4d5851]">
              {new Date(post.scheduled_publish_time).toLocaleString()}
            </span>
          ) : null}
        </div>

        <div className="mt-4">{body}</div>
      </div>
    </article>
  );
}

function TagMultiSelect({
  tags,
  selectedTagIds,
  open,
  onToggleOpen,
  onToggleTag
}: {
  tags: TagDirectoryItem[];
  selectedTagIds: string[];
  open: boolean;
  onToggleOpen: () => void;
  onToggleTag: (tagId: string) => void;
}) {
  return (
    <div className="relative">
      <button
        type="button"
        className="flex w-full items-center justify-between rounded-2xl border border-[#d8d1c6] bg-white px-3 py-2 text-left text-sm transition hover:border-[#0b5d4a] focus:outline-none focus:ring-2 focus:ring-[#d4eee6]"
        onClick={onToggleOpen}
      >
        <span className="flex items-center gap-2">
          <Tag className="h-4 w-4 text-[#0b5d4a]" />
          Tags
          <span className="rounded-full bg-[#e8f3ee] px-2 py-0.5 text-xs font-semibold text-[#0b5d4a]">
            {selectedTagIds.length}
          </span>
        </span>
        <ChevronDown className={`h-4 w-4 transition ${open ? "rotate-180" : ""}`} />
      </button>

      {open ? (
        <div className="absolute z-30 mt-2 w-full rounded-2xl border border-[#d8d1c6] bg-white p-3 shadow-xl">
          <div className="max-h-56 space-y-2 overflow-auto pr-1">
            {tags.length === 0 ? <p className="text-sm text-[#63615b]">No active tags yet.</p> : null}
            {tags.map((tag) => {
              const checked = selectedTagIds.includes(tag.id);
              return (
                <label
                  key={tag.id}
                  className="flex cursor-pointer items-start gap-2 rounded-xl px-2 py-1.5 text-sm transition hover:bg-[#f6f8f4]"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => onToggleTag(tag.id)}
                    className="mt-1 h-4 w-4 rounded border-[#c9c2b7]"
                  />
                  <span className="flex-1">
                    <span className="block font-medium text-[#1b1b1b]">{tag.display_name}</span>
                    <span className="block text-xs text-[#6d6963]">
                      {tag.handle ?? "No handle"} {tag.platform ? `• ${titleCase(tag.platform)}` : ""}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
          <div className="mt-3 flex items-center justify-between">
            <p className="text-xs text-[#6d6963]">{selectedTagIds.length} selected</p>
            <Button variant="outline" onClick={onToggleOpen}>
              Done
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function NavLink({
  label,
  href,
  icon,
  active = false
}: {
  label: string;
  href?: string;
  icon: React.ReactNode;
  active?: boolean;
}) {
  const content = (
    <div
      className={`flex items-center justify-between rounded-2xl px-4 py-3 text-sm transition ${
        active ? "bg-white text-[#111111]" : "text-white/75 hover:bg-white/10 hover:text-white"
      }`}
    >
      <span className="flex items-center gap-3">
        {icon}
        {label}
      </span>
      <ArrowUpRight className="h-4 w-4" />
    </div>
  );

  if (href) {
    return <Link href={href}>{content}</Link>;
  }

  return content;
}

function SectionHeader({
  title,
  body,
  right
}: {
  title: string;
  body: string;
  right?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h3 className="text-2xl font-semibold tracking-tight text-[#111111]">{title}</h3>
        <p className="mt-1 text-sm text-[#63615b]">{body}</p>
      </div>
      {right}
    </div>
  );
}

function BadgeChip({
  children,
  dark = false,
  muted = false
}: {
  children: React.ReactNode;
  dark?: boolean;
  muted?: boolean;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-medium ${
        dark
          ? "bg-black/70 text-white"
          : muted
            ? "bg-[#f1ede6] text-[#5a554f]"
            : "bg-[#e8f3ee] text-[#0b5d4a]"
      }`}
    >
      {children}
    </span>
  );
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="mt-5 rounded-[1.75rem] border border-dashed border-[#cec7bc] bg-white p-8 text-center">
      <BadgeCheck className="mx-auto h-8 w-8 text-[#0b5d4a]" />
      <h4 className="mt-3 text-lg font-semibold">{title}</h4>
      <p className="mt-2 text-sm leading-6 text-[#63615b]">{body}</p>
    </div>
  );
}

function LoadingGrid() {
  return (
    <div className="mt-5 grid grid-cols-1 gap-5 md:grid-cols-3">
      {[1, 2, 3].map((item) => (
        <div key={item} className="overflow-hidden rounded-[1.75rem] border border-[#d9d2c7] bg-white">
          <div className="aspect-square animate-pulse bg-[#e8ebe4]" />
          <div className="space-y-4 p-5">
            <div className="h-4 w-24 animate-pulse rounded-full bg-[#e8ebe4]" />
            <div className="h-28 animate-pulse rounded-2xl bg-[#e8ebe4]" />
            <div className="h-10 animate-pulse rounded-2xl bg-[#e8ebe4]" />
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
  return value
    .toLowerCase()
    .replace(/(^|_)([a-z])/g, (_match, prefix, char) => `${prefix ? " " : ""}${char.toUpperCase()}`);
}

function normalizeAspect(value: string) {
  return ASPECT_OPTIONS.some((ratio) => ratio.value === value) ? value : "SQUARE_1_1";
}

function defaultDraft(post?: Post): DraftState {
  return {
    caption: post?.generated_caption ?? "",
    aspectRatio: normalizeAspect(post?.selected_aspect_ratio ?? "SQUARE_1_1"),
    tagIds: post?.tags.map((tag) => tag.id) ?? [],
    rejectionReason: ""
  };
}

function defaultSchedule(): ScheduleState {
  return { scheduledAt: "", platforms: ["FACEBOOK"] };
}

function ratioToAspectClass(value: string) {
  switch (normalizeAspect(value)) {
    case "PORTRAIT_4_5":
      return "aspect-[4/5]";
    case "LANDSCAPE_16_9":
      return "aspect-video";
    default:
      return "aspect-square";
  }
}

function aspectLabel(value: string) {
  return ASPECT_OPTIONS.find((ratio) => ratio.value === normalizeAspect(value))?.label ?? "1:1";
}
