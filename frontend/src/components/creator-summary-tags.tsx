import type { Schemas } from "@/api/client";
import { Tags } from "@/components/page";

type CreatorSummary = Pick<Schemas["CreatorSummaryView"], "labels" | "tags" | "inactivity_tag">;

export function CreatorSummaryTags({
  creator,
  max,
  emptyText,
}: {
  creator: CreatorSummary;
  max?: number;
  emptyText?: string;
}) {
  const tags = [...creator.labels, ...(creator.tags ?? [])];
  if (creator.inactivity_tag) tags.unshift(creator.inactivity_tag);
  if (!tags.length && emptyText) {
    return <p className="text-muted-foreground text-sm">{emptyText}</p>;
  }
  return <Tags tags={[...new Set(tags)]} max={max} />;
}
