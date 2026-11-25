# Entity co-mention analysis

The entity report exposes an additive associations array, ranked by distinct stored story groups and then signal count. Each entry includes up to three linked examples. Repeated mentions within one signal count once; repeated coverage within a cluster counts as one story group. Case and trailing punctuation are normalized, but separate legal identities are not automatically merged. The CLI prints the ten leading co-mentions; JSON exposes up to thirty. The association_scope field identifies truncated signal sets. These are research leads, not evidence of affiliation, ownership, causation or independent corroboration.

No new network requests, model calls or schema changes.
