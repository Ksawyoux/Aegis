---
title: Connection pool saturation during a regional failover
occurred_at: 2026-03-14T09:20:00Z
services: [payments-api]
---

## Summary

A regional database failover left the payments service holding connections to a replica that no
longer accepted writes. The pool filled with unusable connections and new requests queued until
they timed out.

## Symptoms

Request latency rose sharply while CPU and memory stayed flat. Application logs filled with pool
acquisition timeouts. The error rate climbed only after the pool was already saturated, so the
alert fired several minutes after the underlying change.

## Contributing factors

Pool sizing had been chosen for steady-state traffic and left no headroom for a period where
half the connections were unusable. Health checks tested the listener rather than write
capability, so unhealthy connections were returned to the pool rather than discarded.

## Resolution

Drained the pool, pointed the service at the promoted primary, and restarted the workers. Later
work replaced the liveness check with one that performs a trivial write, so a connection that
cannot serve traffic is evicted rather than recycled.
