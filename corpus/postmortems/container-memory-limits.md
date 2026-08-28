---
title: Indexer restarts after a container memory limit change
occurred_at: 2026-04-02T22:05:00Z
services: [search-api]
---

## Summary

An indexing workload began restarting repeatedly after a routine manifest change. The workload
itself had not changed; the space it was permitted to use had.

## Symptoms

The pod entered a restart loop. Kubernetes reported the container terminated by the kernel out of
memory killer with exit code 137. Application logs ended mid-request with no exception, because the
process was killed rather than failing.

## Contributing factors

Memory limits were tuned by observing steady-state usage, which does not capture the peak during a
segment merge. The workload had no memory-pressure metric of its own, so the only visible signal
was the restart count.

## Resolution

Restored the previous limit and staged the reduction behind a load test that exercises a merge.
Added a container memory working-set alert so pressure is visible before the kernel intervenes.
