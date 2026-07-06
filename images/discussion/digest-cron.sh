#!/usr/bin/env bash
# Hourly digest scheduler for the discussion service (the "default hourly cron").
#
# `discuss-digest` runs ONE pass: it self-selects the subscriptions actually due
# this hour (per-recipient cadence/hour via is_due), holds a run lock, and is
# idempotent per period (delivery ledger) — so exact wall-clock alignment is not
# required, only that a pass fires each hour. This loop sleeps to the top of each
# hour and runs one pass; a failed pass is logged and retried next hour, never
# wedging the loop. Set DISC_DIGEST_ENABLED=0 to disable (compose leaves it out by
# stopping this service; the flag also short-circuits the pass itself).
set -u

echo "digest-cron: hourly digest scheduler starting"
while true; do
  now=$(date +%s)
  next=$(( (now / 3600 + 1) * 3600 ))   # top of the next hour
  sleep $(( next - now ))
  echo "digest-cron: $(date -u +%FT%TZ) running hourly digest pass"
  discuss-digest || echo "digest-cron: pass failed (will retry next hour)"
done
