---
title: Rejected sessions from an off-by-one boundary comparison
occurred_at: 2026-06-11T11:15:00Z
services: [auth-api]
---

## Summary

A refactor of validation logic changed a boundary comparison. Values landing exactly on the
boundary were treated as outside it, and the requests carrying them were rejected.

## Symptoms

A small, steady fraction of requests failed while the great majority succeeded. The failures were
spread evenly across clients rather than concentrated, which ruled out a single bad caller. No
infrastructure metric moved.

## Contributing factors

Tests covered values clearly inside and clearly outside the range and none exactly on it. The
change description described the work as a refactor, so review attention went to structure rather
than to the comparison itself.

## Resolution

Restored the inclusive comparison and added cases at the exact boundary in both directions. Review
guidance now calls out comparison-operator changes as behavioural regardless of how the change is
described.
