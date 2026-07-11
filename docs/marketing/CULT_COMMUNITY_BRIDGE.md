# Positioning: reconciling "Community Intelligence" with "Cult Engineering"

## Context

Two positioning surfaces currently disagree on their core noun. The live site
(`SableWeb/src/components/landing/LandingTerminal.tsx`) leads with **"Community
intelligence infrastructure."** A new marketing page leads with **"Cults, not
Communities."** A prospect who sees both feels whiplash, and someone who knows the
product will notice we grade "community health" with a tool called the **Cult
Grader**.

This plan resolves that without dropping either word. It is a copy plan, not a
redesign. The goal is one coherent brand where "community" and "cult" each have a
clear, separate job.

House rule for all copy below: write like a sharp person, not a model. No
em-dashes, no rule-of-three padding, no grandiose abstractions.

---

## 1. The model: two words, two jobs

The contradiction dissolves when the two words stop competing for the same slot.

- **"Community" is the substrate we measure.** Diagnosing community health,
  tracking community signal, grading the account on real data. This is the
  tooling layer, and "Community intelligence infrastructure" stays true and
  technical for it.
- **"Cult" is the outcome we engineer.** The durable, identity-based end-state a
  healthy community becomes. This is the vision, not the measurement.

This is already how Sable thinks. Three pieces of internal evidence:

1. The acquisition funnel already ends at the cult:
   `follower -> mentioner -> recurring -> quality reply -> "cultist"`
   (`SUITE_CAPABILITIES.md`, `PITCH_DECK.md`, `ONE_PAGERS.md`).
2. The authenticated portal subtitle already carries both words:
   **"Community intelligence & cult engineering operations."**
   (`AuthenticatedDashboard.tsx`).
3. The flagship diagnostic is a community-health tool *named for the cult
   outcome*: the **Cult Grader**.

So we are not inventing a rebrand. We are making an existing instinct consistent
and public.

---

## 2. The bridge primitive (write once, reuse everywhere)

"Community" is overloaded. Depending on who says it, it means an audience, a
scene, an ideology, or an actual high-commitment cult. That ambiguity is the whole
problem. The fix is to name the overload out loud and then claim the precise
variant.

Canonical bridge copy (adapt length per surface, keep the logic):

> "Community" means four different things. An audience. A scene. A shared
> ideology. A cult. Most crypto "community building" produces the first one, and
> an audience leaves the moment the chart turns. We build the last one. We mean
> high-commitment shared identity, not a leader and his followers. We call it what
> it is: a cult.

Rule: on any **cold** surface, the bridge appears *before* the provocation. A
first-time reader should understand what we mean by "cult" before we start using
"cabal" and "cultist."

---

## 3. Two registers, one brand

We keep two front doors, and they do different jobs. That is fine as long as they
share the bridge.

- **The terminal landing** (`LandingTerminal.tsx`) stays the understated
  infrastructure door. Restrained, technical, monospace, dark. It is for people
  who already get it, plus the sign-in.
- **The manifesto** (the "Cults, not Communities" page) is the loud vision
  surface for cold prospects who need the thesis argued.

Guardrail against brand schizophrenia: both surfaces resolve to the same two-layer
model, and the terminal links to the manifesto rather than restating it loudly.

---

## 4. Hard constraint: the client-facing wall stays

Today, "cult" is deliberately sanitized out of client-facing *member* labeling.
`cultist_candidate` renders to clients as **"emerging leader" / "Champions"**
(`Badge.tsx`, `src/types/index.ts`). Keep that.

The distinction the plan enforces:

- **Brand and vision level: "cult" is public and fine.** Cult Grader, Cult
  Engineering, the manifesto, the case-study "Cult Effect."
- **Per-member client level: stays sanitized.** We never call a specific client's
  actual member a "cultist" to their face. The public cult brand must not leak
  into the per-member client surfaces.

---

## 5. Surface-by-surface changes

### 5.1 Live terminal landing — `SableWeb/src/components/landing/LandingTerminal.tsx`

The one file that owns the public hero. Copy is hardcoded JSX. Recommended is the
minimal-touch option; it plants "cult" with a one-word change and mirrors the
portal.

**Recommended (minimal):**
- Tagline: keep `Community intelligence infrastructure.` (true measurement layer)
- Description: change the final clause from `...cultural signal tracking, and
  growth engineering.` to `...cultural signal tracking, and cult engineering.`
- Add a small linked line near the CTA: `Cults, not communities ->` linking to the
  manifesto surface (see 5.4).

**Alternative (fuller, optional):**
- Tagline becomes a two-line type sequence:
  `Community intelligence infrastructure.` then `We build cults, not communities.`

### 5.2 Metadata sync (do this whenever the tagline changes)

These repeat `Sable — Community Intelligence` and must not drift from the hero:
- `SableWeb/src/app/layout.tsx` (title, OG, twitter)
- `SableWeb/src/app/opengraph-image.tsx` (generated PNG tagline)
- `SableWeb/src/app/manifest.ts`

Recommendation: keep the `title` as `Sable — Community Intelligence` for search,
and update the OG/twitter `description` to end on cult engineering so shared links
plant the concept. Example OG description: `Community health diagnostics and
cultural signal tracking. We engineer cults, not communities.`

### 5.3 Authenticated portal — `AuthenticatedDashboard.tsx`

Already says `Community intelligence & cult engineering operations.` No change.
Use this line as the reference phrasing everything else aligns to.

### 5.4 The manifesto page ("Cults, not Communities")

Two copy edits plus one structural decision.

- **Soften the wholesale rejection into the overload-teaching move.** The current
  open ("Crypto projects don't need community building. They need cult
  engineering.") reads as attacking the word "community," which fights our own
  product identity. Replace with the bridge (section 2). Suggested open:
  > "Community" is one word for very different things: an audience, a scene, an
  > ideology, a cult. Most crypto "community building" produces an audience. We
  > build the last kind, and we call it what it is.
- **Density cap.** Keep "cult engineering" as the hook. Thin the "cabal /
  cultists" repetition so the concept lands without the baggage that a
  reputation-conscious, well-funded founder will flinch at.
- **Structural decision (open):** where does this page live? There is no `/about`
  or `/pricing` route in SableWeb today. Options: host it as `/manifesto` and link
  from the terminal, make it the cold-traffic landing while the terminal becomes
  the app door, or keep it as a standalone hand-delivered prospectus. Depends on
  how the doc gets used. Wire its CTA to the existing `/intake` free diagnostic
  (see 5.6).

### 5.5 Canonical docs — `SablePlatform/docs/marketing/`

`MESSAGING.md` is the governing file ("if a slide conflicts, this file wins"), so
lock the bridge there and everything else inherits it.

- Add a short **"Community vs Cult"** rule to `MESSAGING.md`: the two-layer model
  (section 1), the bridge primitive (section 2), and the client-facing wall
  (section 4).
- Update the canonical one-liner in `marketing/README.md` and
  `SUITE_CAPABILITIES.md` so "community" (measured) and "cult" (built) both appear
  with their jobs, instead of "community-growth operation" alone.
- Deck: `PITCH_DECK.md` slide 3 ("What Sable is") gets the same one-liner. The
  funnel slide already ends at "cultist," so it is consistent.

### 5.6 Connect the free-diagnostic wedge

The `/intake` "Free Community Diagnostic" page already exists and is the honest,
low-friction entry point. The manifesto and the terminal should both route to it.
Note the naming: the public wedge stays **"Free Community Diagnostic"** (community
is the measured substrate), which is on-model. Do not rename it to "cult."

---

## 6. Guardrails checklist

- Bridge before provocation on every cold surface.
- Density cap on "cabal / cultist" outside the manifesto.
- Client per-member labeling stays sanitized ("emerging leader" / "Champions").
- Keep "measured work and measured amplification, never a guaranteed outcome."
- The public wedge stays "Free Community Diagnostic."

---

## 7. Sequencing

1. **Lock the primitive.** Add the "Community vs Cult" rule to `MESSAGING.md`.
2. **Terminal + metadata.** Small, high-visibility. One-word description change,
   optional linked line, sync the three metadata files.
3. **Manifesto page.** Overload-teaching open, density cap, CTA to `/intake`, host
   decision.
4. **Propagate.** README one-liner, SUITE_CAPABILITIES, deck slide 3.

---

## 8. Open decisions for Sieggy

- Host route for the manifesto (`/manifesto`, cold-traffic landing, or standalone
  prospectus).
- How hard to push "cult" on the cold terminal (minimal one-word plant vs the
  fuller two-line tagline).
- Whether the metadata `title` changes or stays `Community Intelligence` for
  search.
