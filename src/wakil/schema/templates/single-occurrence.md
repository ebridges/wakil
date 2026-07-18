Single-occurrence record — for a note describing one dated event or
standalone artifact, not an accumulating subject. There is no Timeline: a
running log of one occurrence is just the occurrence restated, so don't use
the Compiled-Truth/Timeline split here.

Not every section below applies to every type using this shape — a meeting
almost always has action items; a reflection or an original idea almost
never does. Include a section only when the source actually supports it;
omit it rather than writing a placeholder like "None" for a section that
doesn't fit the type at all. (For a type like `meeting`, where the section
usually applies but happened not to fire this time, state "None" explicitly
instead of omitting it — that distinguishes "checked, found nothing" from
"not checked.")

Skeleton:

```markdown
# {Title}

## Summary

{the load-bearing outcomes or content, cited}

## Key Decisions

{only for types where deciding something is in scope — e.g. a meeting;
state "None" explicitly if the type usually has this section but didn't
this time}

## Action Items

{only if there are concrete next steps; omit the section entirely if the
type doesn't produce these}

## Discussion Notes

{supporting detail, and any analysis of why this mattered}

## Open Questions

{only if something genuinely unresolved carries forward — a pending
decision, a follow-up not yet raised, an ambiguity the source didn't
settle; omit the section entirely rather than leaving it empty. This is
distinct from Discussion Notes: it's what's still open, not analysis of
what already happened.}
```
