---
title: Origin overload after a cache policy change
occurred_at: 2026-05-19T16:40:00Z
services: [cdn-api]
---

## Summary

A content delivery configuration change reduced how long responses were considered fresh. Traffic
that had been served from the edge began arriving at the origin instead.

## Symptoms

Origin request volume rose by an order of magnitude within minutes while total client traffic was
unchanged. Edge hit ratio fell. Origin latency degraded once its own capacity was exhausted, and
errors followed the saturation rather than causing it.

## Contributing factors

Cache configuration lived outside the deployment pipeline, so the change did not appear in any
release. Nothing alerted on hit ratio, which is the metric that would have shown the cause directly
rather than its downstream effect.

## Resolution

Reverted the policy and let the edge refill. Cache configuration was moved under the same review
and audit path as application changes, and a hit-ratio alert was added.
