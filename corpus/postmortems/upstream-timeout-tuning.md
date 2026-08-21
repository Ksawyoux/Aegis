---
title: Gateway errors after an upstream timeout reduction
occurred_at: 2026-07-08T14:30:00Z
services: [checkout-api]
---

## Summary

A client configuration change lowered how long the service would wait for an upstream dependency.
Requests that had previously completed slowly began failing instead.

## Symptoms

Server error responses rose within a couple of minutes of the rollout. The upstream service was
healthy throughout and its own error rate never moved, which is what distinguished this from an
upstream outage.

## Contributing factors

The timeout was chosen from median upstream latency rather than from a high percentile, so a
routine slow path exceeded it. The change was bundled with unrelated work, so its blast radius was
not considered on its own.

## Resolution

Restored the previous timeout and re-derived it from the ninety-ninth percentile with headroom.
Timeout and retry values are now called out explicitly in review.
