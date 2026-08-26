# 14. Structural similarity is a three-tier ladder; WL runs on connected cores

- Status: accepted
- Date: 2026-08-25 (core-first correction 2026-08-25)
- Deciders: design by Claude; retraction confirmed against measurements
- Related: lp2graph issue #49 (WL tier for M6)

## Context and problem statement

"Same architecture across papers" needs a similarity notion. Exact schema
isomorphism (M6) is identity-or-nothing — measured on 29 canonical models it
yields only singletons. Paper-level feature fingerprints never look at the
graph. Something graded and graph-native is needed between them.

## Decision

Three tiers, each with its own instrument and honest scope:

1. **Fingerprints** (`corpusbuilder.fingerprint`): 20 deterministic features
   per paper, cosine + average-linkage agglomerative, silhouette-scanned k,
   labelled *pre-canonical* throughout. Corpus-wide (238 papers).
2. **WL subtree features** (`corpusbuilder.wlcluster`): Weisfeiler–Lehman
   hashes on exactly the typed graphs M6 matches (`cls|subtype`,
   `type|role`), cosine over the hash counts — a graded relaxation of the
   same equivalence, and an explicit vector embedding (k-means-able).
   **Computed on connected cores by default.** Measured: two papers whose
   sidecars each declare many unreferenced indices score 0.90 on full graphs
   but 0.05 on cores — identical iteration-0 hashes of declared-only
   scaffolding masquerade as structure. The full-graph value is recorded
   alongside for comparison, never headlined.
3. **Exact isomorphism** (M6): the identity tier.

## Consequences

The PESP pair is the ladder's proof: exact iso "different" (one unused
declared index), core-WL 1.00. A previously reported cross-paper twin at
full-WL 0.90 was retracted as scaffolding artifact. Depth-resolved WL
(iteration 0 vs ≤3) distinguishes shared anatomy from shared fine wiring —
the similarity-without-identity reading (crew pairing ~ vehicle scheduling,
0.51 → 0.21).
