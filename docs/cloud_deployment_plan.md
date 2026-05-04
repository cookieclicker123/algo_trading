# NewsFlash Cloud Deployment Plan

Plain-English plan for moving NewsFlash off the laptop into a cheap, low-latency cloud box near Alpaca, while keeping the "edit locally, push in 60 seconds" workflow. Written 2026-05-04.

---

## TL;DR

1. **Fix the memory leak surface first** (a half-day of work). The cloud box you'll want is a $5/month, 1 GB box — it has to fit. Right now peak usage is 500 MB – 1 GB and growing in places it shouldn't.
2. **Host on Fly.io in the `iad` region** (Ashburn, Virginia). That's the same metro as Alpaca's AWS `us-east-1` and Benzinga's edge. Roughly **$4–8/month** for a 1 GB machine. Same-region latency to Alpaca is sub-1ms.
3. **Containerise with a small Dockerfile**, keep state on a 3 GB persistent volume, secrets in `fly secrets`. One command (`fly deploy`) pushes a new build from the laptop in ~45–60 seconds. You can do this mid-premarket safely because the deploy strategy is rolling and the app already cleanly stops/starts its websockets.
4. **Don't bother stopping the VM during market hours.** The app already idles its Benzinga websocket from 9:30 AM–4 PM ET, and a 1 GB machine sitting idle costs pennies. Auto-stop adds operational friction for ~$2/month savings.
5. **Skip the heavy stuff:** no Kubernetes, no AWS account, no Terraform, no CI/CD pipeline, no observability stack. You are one person, one trader, one box.

---

## Step 1 — Memory work (do this BEFORE moving)

Why first: the cloud box you want is small. The audit found three places where memory grows unboundedly during news bursts. None are hard fixes.

### 1.1 Bound the streaming quote/trade caches

`src/newsflash/infra/brokerage/stream_manager.py:95-102` keeps a `deque(maxlen=1000)` of quotes **per symbol**. During premarket bursts the system can subscribe to 300–400 tickers at once. At ~700 bytes per quote tick, that's **~280 MB just in quote history**, and most of those quotes are never read again after the first ~30 seconds.

**Fix:** drop `maxlen` from 1000 to 100 (or even 50). The active consumers (surge monitor, NBBO confirmations) only look at the last few seconds. This alone reclaims ~200 MB at peak.

### 1.2 Tighten RecordManager pending TTLs

`src/newsflash/shared/statistics/record_manager.py:100-123` holds 5–6 dicts (`_pending_metadata`, `_pending_classifications`, etc.) with TTLs of **1–4 hours**. That's calibrated for "we might want to attribute a late metadata fetch to an article from 3 hours ago." For trading purposes you don't — anything past ~15 minutes is no longer useful for recall analysis.

**Fix:** drop the longest TTLs to 30 minutes. Saves another 100–200 MB across a typical premarket session.

### 1.3 Cap the yfinance coordinator queue

The audit flagged `YahooFinanceCoordinator`'s request queue as unbounded. If yfinance stalls (which it does), the queue can balloon to hundreds of MB.

**Fix:** add a `maxsize` to the queue and drop oldest-first when full. A failed metadata lookup is not catastrophic — the prefilter will just skip the article.

### 1.4 Rotate the audit logs

`tmp/audit_logs/` is 10 GB and growing forever. On a 3 GB cloud volume that fills up in days.

**Fix:** keep last 7 days only, gzip anything older than 24h. One small async housekeeping task at the end of each session.

**Net effect** of 1.1–1.4: peak RAM drops from ~1 GB to ~300–400 MB, and disk stays under 2 GB indefinitely. Now we fit on a $5 box.

---

## Step 2 — Pick the host

The non-negotiable is **AWS `us-east-1` proximity** because that's where Alpaca's API and trading infrastructure live. Benzinga's news websocket also originates from US-east. Anything in Europe or Asia adds 80–150ms round-trip per quote and per order — fatal for a latency-sensitive momentum strategy.

### Recommended: Fly.io, region `iad`

Fly's `iad` region is in Ashburn, VA — the same metro as AWS `us-east-1`. Measured latency from Fly `iad` to Alpaca's API is consistently sub-1ms, basically indistinguishable from EC2.

**Why Fly over AWS:**
- One-line deploys (`fly deploy`) from your laptop. No SSH, no rsync, no CI.
- Built-in persistent volumes, secrets, log streaming.
- Pricing is honest and small: ~$1.94/mo for the CPU, ~$5/mo for 1 GB RAM, ~$0.45/mo for a 3 GB volume. **All-in roughly $7–8/month.**
- Rolling deploys mean a `fly deploy` mid-premarket briefly bounces the websocket (~5s) but doesn't drop your open positions, because position state is stored in Alpaca, not in NewsFlash memory.

### Runner-up: AWS Lightsail (`us-east-1`)

If you specifically want AWS: Lightsail $5/mo plan = 1 vCPU + 1 GB RAM + 40 GB SSD, in `us-east-1`. Same latency to Alpaca. Less polished deploy flow — you'd SSH in and `git pull` or use a small `rsync` script. Works fine, just a bit less elegant than Fly.

### Don't use

- Hetzner (cheap but Frankfurt/Helsinki — too far from Alpaca).
- Render/Railway (no IAD-equivalent at the cheap tier; pricier).
- DigitalOcean NYC (close to Alpaca but Fly is cheaper and has better deploy ergonomics).

---

## Step 3 — Containerise

A minimal Dockerfile is enough. No multi-stage cleverness, no Alpine — just `python:3.11-slim` and `uv pip install`.

```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml ./
RUN uv pip install --system -e .
COPY . .
CMD ["python", "-m", "uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

Add a `.dockerignore` that excludes `tmp/`, `data/cache/`, `.venv/`, `tests/`, `.git/` so the image stays under 250 MB.

`fly.toml` (created by `fly launch`) needs three things customised:
- `primary_region = "iad"`
- A `[[mounts]]` block pointing `/app/data` and `/app/tmp` at a persistent volume.
- VM size: `shared-cpu-1x`, `memory_mb = 1024`.

---

## Step 4 — State, secrets, and what lives where

| Thing | Where it lives | Survives redeploy? |
|---|---|---|
| `data/blacklist.json` | persistent volume | yes |
| `data/cache/permanent_metadata.json` | persistent volume | yes |
| `data/cache/daily_metadata.json` | persistent volume | yes (rebuilt at 4 AM ET anyway) |
| `tmp/statistics/` | persistent volume | yes |
| `tmp/audit_logs/` | persistent volume (7-day rotation) | yes, recent only |
| `.env` values | `fly secrets set ALPACA_KEY=...` | yes (not in image) |
| Open positions / orders | Alpaca's servers | yes (not our problem) |

Critical: never bake secrets into the image. Use `fly secrets set` once, then forget. The list to set:
`ALPACA_KEY`, `ALPACA_SECRET`, `BENZINGA_API_KEY`, `GROQ_API_KEY`, `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_BOT_TOKEN_2`, `TELEGRAM_CHAT_ID_2`.

---

## Step 5 — The "edit locally, push mid-premarket" workflow

This is the part you specifically asked about. The flow once set up:

```bash
# edit code in cursor as normal
# when you want to ship:
fly deploy
# ~45 seconds later it's live in iad
```

What happens during deploy:
1. Fly builds the image locally (or remotely if you pass `--remote-only`).
2. Pushes to Fly's registry.
3. Starts a new VM, runs healthcheck against `/health`.
4. Once healthy, drains the old VM (current websocket disconnects cleanly).
5. New VM picks up the websocket subscription.

Total downtime: ~5–10 seconds where Benzinga websocket isn't connected. **You will miss any article that arrives in that window.** That's the one rule: don't redeploy when you can see breaking news on the wire. Outside that, mid-premarket deploys are safe — open positions are tracked by Alpaca, and the position-manager re-attaches on startup.

For tighter iteration on non-trading-path code (e.g. tweaking a sector prompt), `fly deploy` is fine. For experimental changes to the trading path itself, run them locally against paper trading first, then deploy.

### Streaming logs from the laptop

```bash
fly logs    # live tail
```

Same Telegram notifications you have today still arrive on your phone — no change.

---

## Step 6 — Idle behavior and cost

The app already idles its Benzinga websocket during market hours and overnight (`MarketHoursScheduler`). On a 1 GB Fly machine, idle CPU is negligible — you're paying for the RAM allocation and disk regardless. **Don't bother auto-stopping the VM.** The savings (~$2/mo) aren't worth the complexity of restart timing relative to the 4 AM ET premarket open.

**Estimated all-in monthly cost:**
- Fly machine (shared-cpu-1x, 1 GB): ~$5.70
- Fly volume (3 GB): ~$0.45
- Egress (Telegram + REST calls, low volume): ~$0–1
- **Total: ~$6–8/month.**

Anthropic + Groq + Alpaca + Benzinga API costs are unchanged from local — those are usage-based and independent of where the code runs.

---

## Step 7 — Cutover order (suggested 2-day plan)

**Day 1 (do locally, no cloud yet):**
1. Memory fixes 1.1 → 1.4 above. Verify with `python -m src.main` during a premarket session: peak RAM should stay under 500 MB.
2. Add `Dockerfile`, `.dockerignore`. `docker build .` to confirm it works.
3. Run the container locally for one premarket session as a final smoke test.

**Day 2:**
4. `fly launch` (creates app + `fly.toml`). Edit region to `iad`, attach a 3 GB volume.
5. `fly secrets set ...` for all env vars.
6. Manually copy over `data/blacklist.json` and `data/cache/permanent_metadata.json` to the volume (`fly ssh sftp`).
7. `fly deploy`. Watch `fly logs` through the next premarket session.
8. Once it's run cleanly for two sessions, switch off the laptop. Keep the local environment as your dev/test sandbox only.

---

## What this plan deliberately does NOT include

- **No CI/CD.** You'll deploy from your laptop. One person, one trader.
- **No observability stack** (Datadog, Grafana, etc.). Telegram + `fly logs` is enough at this scale.
- **No multi-region failover.** If `iad` goes down for 30 minutes, you miss a session. That's an acceptable loss vs. the operational complexity of a hot standby.
- **No Kubernetes / ECS / Terraform.** Anti-features at this size.
- **No paper-trading staging environment.** Use your laptop for that.
- **No database migration.** JSON files on a persistent volume are fine for the data volumes here. Revisit if `tmp/statistics/` ever pushes past a few GB even with rotation.

---

## Open questions to confirm before starting

1. Are you comfortable with Fly.io as the host, or do you want me to draft the AWS Lightsail equivalent instead?
2. Do you want me to do the memory work as one PR or split it (the four fixes are independent)?
3. The 5–10s deploy gap during a redeploy — acceptable, or do you want a blue/green setup that holds two websockets briefly? (More complex, probably overkill.)
