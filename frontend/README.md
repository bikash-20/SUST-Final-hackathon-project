# Web Frontend

Next.js 16 App Router frontend for the multi-provider decision-support
prototype. It uses Zustand for shared live state, TanStack Query for
coordination mutations, and Recharts for observed provider-balance history.

## Run

Start the FastAPI backend on port 8000 first, then:

```bash
cp .env.example .env.local
npm ci
npm run dev
```

Open `http://localhost:3000`. The Next.js server proxies `/v1/*` to
`NEXT_PUBLIC_BACKEND_URL` (default `http://localhost:8000`), including the SSE
stream, so browser traffic stays same-origin.

## Live data flow

`Providers.tsx` mounts one persistent `EventSource` above the role views. The
telemetry client registers named listeners for `snapshot`, `ready`, tick
outcomes, and coordination state events instead of relying on the default SSE
message channel. A snapshot hydrates current shared cash, three provider
positions, and coordination cases before incremental events arrive.

The role switcher renders three views without reconnecting:

- **Agent Mobile** — live shared cash, provider-separated e-money, freshness,
  confidence, recent synthetic activity, and safe advisories.
- **Ops Web** — observed balance trajectories, EWMA forecast evidence, and
  `PENDING -> ACKNOWLEDGED -> RESOLVED` controls.
- **Risk Reviewer** — 12-minute detector evidence, confidence, rationale, and
  possible benign explanations.

An inconsistency event below the `0.50` confidence threshold activates the
uncertainty-safe layout. No balance shown by these views is a hardcoded demo
primitive; absent telemetry renders an explicit waiting state.

## Verify

```bash
npm run typecheck
npm run lint
npm run build
npm audit --omit=dev
```

The production server can be exercised after a successful build with
`npm run start`.
