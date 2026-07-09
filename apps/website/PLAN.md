# tokemetry website implementation plan

This document defines the planned public website for tokemetry. It is intentionally a planning artifact only: no website runtime, build configuration, or deployment workflow is implemented here yet.

## Decision

Build the website inside this monorepo at `apps/website`.

Do not create a separate repository for the first public site. The tokemetry repository already follows a monorepo structure with `apps/*` and `packages/*`, and the website should share the same brand assets, release context, issue tracker, and future documentation pipeline.

A separate website repository only becomes justified if the site later has a separate team, CMS, design system, publication workflow, or deployment cadence.

## Website mission

The website should explain tokemetry as a serious developer infrastructure product:

> Self-hosted AI token telemetry for developers running coding assistants across multiple machines.

The landing page must answer five questions quickly:

1. What problem does tokemetry solve?
2. Why does this matter for Claude Code subscription users?
3. How does it work technically?
4. Why is it safe/private?
5. How can a developer install, self-host, or follow the project?

## Primary audience

### Initial audience

- Senior developers using Claude Code heavily.
- Developers using multiple machines under one Pro/Max subscription.
- AI power users who care about burn-rate, reset windows, weekly caps, and cost/value visibility.
- Self-hosting users comfortable with Docker, VPS, WireGuard, and GitHub.

### Later audience

- Teams using multiple AI coding providers.
- Developers integrating usage data into third-party tools.
- Open-source contributors building provider adapters.
- Users who want Grafana or API-level access to usage telemetry.

## Core positioning

Tokemetry is not a generic dashboard. It is an observability layer for AI coding usage.

Recommended positioning language:

- Self-hosted AI token telemetry.
- Multi-machine usage aggregation.
- Burn-rate and limit-window monitoring.
- Privacy-first collector architecture.
- API-first usage analytics.
- Claude Code first, multi-provider by design.

Avoid these positioning traps:

- Do not present it as a crypto/token product.
- Do not make it look like a generic SaaS analytics tool.
- Do not over-emphasize cost tracking at the expense of limit monitoring.
- Do not imply official Anthropic support for undocumented endpoints.
- Do not market it as enterprise compliance software.

## Recommended stack

Use a small static-first site deployed with Cloudflare Workers Static Assets.

Recommended implementation stack:

- `Astro` for content-first static pages.
- `TypeScript` for strict implementation hygiene.
- `Cloudflare Workers Static Assets` for deployment.
- A tiny Worker script only where needed for security headers, redirects, `robots.txt`, future API endpoints, or dynamic Open Graph image generation.
- No database in v1.
- No CMS in v1.
- No heavy client-side framework in v1.

Why Astro instead of a full SPA:

- The website is mostly marketing/docs content.
- Static HTML gives better SEO, performance, and accessibility.
- Interactive product demos can be added as isolated islands later.
- The dashboard itself can remain Vue 3 under `apps/dashboard`.

If implementation speed matters more than content architecture, a Vite-only static site is acceptable, but Astro is the better long-term default for this site.

## Cloudflare deployment model

Preferred target: Cloudflare Workers with Static Assets.

Rationale:

- Static assets and Worker code can be deployed together as one unit.
- Wrangler can upload the configured `dist` directory during deployment.
- A Worker can selectively handle dynamic paths while static files are served directly.
- Cloudflare Git integration can later connect the Worker to this GitHub repository and deploy on push.

Expected deployment path:

```text
apps/website
  package.json
  astro.config.mjs
  wrangler.jsonc
  src/
  public/
  dist/                 # generated, not committed
```

Draft `wrangler.jsonc` shape:

```jsonc
{
  "$schema": "./node_modules/wrangler/config-schema.json",
  "name": "tokemetry-website",
  "main": "./src/worker.ts",
  "compatibility_date": "2026-07-09",
  "assets": {
    "directory": "./dist",
    "binding": "ASSETS"
  }
}
```

If the site is implemented as a multi-route static site rather than an SPA, prefer custom `404.html` behavior over `not_found_handling = "single-page-application"`. If the site later becomes an SPA, set `not_found_handling` explicitly.

Potential Cloudflare settings:

```text
Root directory: apps/website
Build command: npm ci && npm run build
Deploy command: npx wrangler deploy
Output directory: dist
Production branch: master
Preview branches: pull requests / non-master branches
```

## Monorepo integration

Recommended repo layout after implementation:

```text
assets/brand/
  tokemetry-logo-horizontal-dark.svg
  tokemetry-logo-horizontal-light.svg
  tokemetry-logo-vertical-dark.svg
  tokemetry-logo-vertical-light.svg
  tokemetry-logo-monochrome-dark.svg
  tokemetry-icon-dark.svg
  tokemetry-icon-light.svg
  favicon.svg

apps/website/
  PLAN.md
  README.md
  package.json
  astro.config.mjs
  wrangler.jsonc
  public/
    brand/              # generated or copied from assets/brand
    favicon.svg         # copied from assets/brand/favicon.svg
    og-image.png        # generated later
  src/
    content/
    layouts/
    components/
    pages/
    styles/
    worker.ts
    env.d.ts
  scripts/
    sync-brand-assets.mjs
```

Do not manually duplicate brand SVG source files in multiple places. Keep `assets/brand` canonical and copy/sync selected assets into `apps/website/public/brand` during the website build.

Recommended command:

```text
npm run sync:brand && astro build && wrangler deploy
```

## Information architecture

### v1 pages

```text
/                         Landing page
/docs                     Lightweight documentation index
/docs/architecture        Architecture overview
/docs/security            Privacy and security model
/docs/deployment          Self-hosting deployment overview
/docs/api                 API-first design overview
/changelog                Manual changelog or release notes placeholder
/brand                    Brand assets page, optional
```

### Later pages

```text
/docs/collector           Collector install guide
/docs/server              Server install guide
/docs/dashboard           Dashboard guide
/docs/provider-adapters   Provider adapter development guide
/docs/alerts              Alerting configuration guide
/docs/grafana             Grafana datasource guide
/docs/openclaw            OpenClaw integration guide
/blog                     Product notes and technical articles
```

## Landing page structure

### 1. Hero

Goal: explain the product in one screen.

Recommended copy direction:

```text
Self-hosted AI token telemetry.
Track Claude Code usage across every machine, monitor subscription limits, and predict burn-rate before you hit the wall.
```

Hero CTAs:

- `View on GitHub`
- `Read the architecture`
- Later: `Install collector`

Hero visual:

- Use `tokemetry-logo-horizontal-dark.svg` in the header.
- Use a mock dashboard card showing 5-hour block utilization, weekly cap, burn-rate, reset countdown, and machine activity.
- Do not use real user data.

### 2. Problem section

Explain the three core problems:

1. Claude Code local usage history disappears after retention.
2. No cross-machine aggregation.
3. Subscription users need limit-window monitoring, not just token totals.

Tone: concise, technical, slightly direct.

### 3. Solution section

Explain the data flow:

```text
Collectors on every machine parse local usage metadata, queue offline-safe events, and upload normalized counters to your private server. The server deduplicates, prices, rolls up, predicts burn-rate, and serves a dashboard plus API.
```

Visual: simple flow diagram.

```text
Claude Code JSONL / stats cache / OAuth limit source
        ↓
Collector daemon + local SQLite queue
        ↓ WireGuard / HTTPS
FastAPI server + Postgres
        ↓
Dashboard / REST API / WebSocket / alerts
```

### 4. Core feature cards

Cards:

- Multi-machine aggregation
- 5-hour and weekly cap visibility
- Burn-rate prediction
- Provenance-tagged numbers
- Privacy-first collection
- API-first architecture
- Multi-provider abstractions
- Alerting over ntfy, Telegram, SMTP

Each card should be short. Avoid verbose product copy.

### 5. Privacy section

Key message:

```text
Conversation content never leaves the machine.
```

Bullets:

- Uploads usage metadata and counters only.
- OAuth token never leaves the collector machine.
- Server is intended to run behind WireGuard.
- API tokens are bearer-token authenticated and stored hashed.
- Every stored number carries provenance.

### 6. Architecture section

Show the monorepo and runtime architecture.

Emphasize:

- `packages/core`
- `apps/collector`
- `apps/server`
- `apps/dashboard`
- `apps/website`

### 7. Developer callout

Goal: make it attractive to open-source developers.

Potential copy:

```text
Built as infrastructure, not a toy script.
Python 3.12, FastAPI, Postgres, Vue 3 dashboard, OOP provider adapters, strict quality gates.
```

### 8. Roadmap section

Use a condensed version of the project phases:

1. Core + ingest
2. Collector
3. Analytics API
4. Dashboard
5. Alerting
6. Deployment packaging

### 9. Final CTA

- GitHub repository
- Documentation
- Star/follow project
- Later: release binaries or install guide

## Visual direction

Use the generated brand system in `assets/brand`.

### Colors

Recommended CSS variables:

```css
:root {
  --tm-bg: #0B111C;
  --tm-bg-elevated: #111827;
  --tm-surface: #151E2D;
  --tm-text: #F5F7FA;
  --tm-text-muted: #9CA3AF;
  --tm-cyan: #27D8F3;
  --tm-green: #45E08D;
  --tm-frame: #235F6C;
  --tm-border: rgba(148, 163, 184, 0.18);
}
```

Light theme variables:

```css
:root[data-theme="light"] {
  --tm-bg: #F8FAFC;
  --tm-bg-elevated: #FFFFFF;
  --tm-surface: #FFFFFF;
  --tm-text: #111827;
  --tm-text-muted: #4B5563;
  --tm-cyan: #1CC7D8;
  --tm-green: #2FBF82;
  --tm-frame: #143D46;
  --tm-border: rgba(15, 23, 42, 0.12);
}
```

### Typography

Default plan:

- Use system UI stack for the first implementation.
- Avoid bundling commercial font files.
- Later evaluate `Inter`, `IBM Plex Sans`, or `Geist` if licensing and visual fit are acceptable.

Suggested CSS:

```css
font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
  "Segoe UI", sans-serif;
```

### Components

Initial components:

- `SiteHeader`
- `SiteFooter`
- `HeroSection`
- `FeatureCard`
- `ArchitectureDiagram`
- `DashboardMock`
- `CodeBlock`
- `Callout`
- `RoadmapTimeline`
- `MetricBadge`
- `LimitGaugeMock`

## Content details

### Header navigation

```text
Product
Architecture
Security
Docs
GitHub
```

Keep the nav short. This is a technical product, not a marketing-heavy SaaS.

### Footer

Footer links:

- GitHub
- Docs
- Security
- License
- Brand assets

Include a small line:

```text
tokemetry is self-hosted infrastructure. No usage data is sent to a hosted tokemetry service.
```

### Docs tone

Instructional and precise. Avoid hype. Prefer diagrams, code snippets, and operational checklists.

## SEO plan

Initial metadata:

```text
Title: tokemetry — Self-hosted AI token telemetry
Description: Track AI coding token usage across machines, monitor Claude Code subscription limits, and predict burn-rate from your own self-hosted dashboard.
```

Primary keywords:

- AI token tracking
- Claude Code usage monitor
- Claude Code token usage
- AI coding assistant telemetry
- self-hosted token dashboard
- AI usage observability
- Claude Code limit monitor

Open Graph:

- Generate `public/og-image.png` from the horizontal dark logo and a dashboard mock.
- Use 1200x630.
- Include concise text only.

Technical SEO:

- Static HTML for all public docs pages.
- `sitemap.xml` generated at build time.
- `robots.txt` committed/generated.
- Canonical URLs once final domain is selected.

## Accessibility requirements

Minimum acceptance:

- Lighthouse accessibility score 95+.
- Keyboard navigable header and CTAs.
- Visible focus states.
- Sufficient contrast on dark and light surfaces.
- No color-only status communication.
- Motion reduced under `prefers-reduced-motion`.
- All diagrams have text equivalents.

## Performance requirements

Target:

- Lighthouse performance 95+ on desktop and mobile.
- No heavy client-side JavaScript for v1.
- Logo SVGs optimized.
- Dashboard mock built with CSS/SVG, not large raster screenshots.
- Images lazy-loaded below the fold.
- Avoid external analytics scripts unless deliberately selected.

## Security and privacy requirements

Website v1 should not collect personal data by default.

Recommended headers from Worker:

```text
Strict-Transport-Security
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: camera=(), microphone=(), geolocation=()
Content-Security-Policy
```

If analytics are added:

- Prefer Cloudflare Web Analytics or a privacy-preserving alternative.
- Document what is collected.
- Avoid cookies unless needed.

## Worker behavior plan

Initial Worker responsibilities:

1. Serve static assets through the `ASSETS` binding.
2. Add security headers.
3. Redirect legacy or alternate paths.
4. Serve `robots.txt` or `security.txt` if not static.
5. Future: generate Open Graph images or expose a small `/api/project-status` endpoint.

Do not put product API logic in the website Worker. The production tokemetry server API belongs to `apps/server`.

## Development scripts

Draft scripts:

```json
{
  "scripts": {
    "dev": "astro dev",
    "build": "npm run sync:brand && astro build",
    "preview": "wrangler dev",
    "deploy": "npm run build && wrangler deploy",
    "sync:brand": "node scripts/sync-brand-assets.mjs",
    "check": "astro check && tsc --noEmit",
    "lint": "eslint .",
    "format": "prettier --check ."
  }
}
```

## CI/CD plan

### GitHub checks

Add later under `.github/workflows/website.yml`:

- Install Node.
- Install dependencies under `apps/website`.
- Run `npm run check`.
- Run `npm run build`.
- Optionally run Lighthouse CI against the built site.

### Cloudflare deployment

Preferred deployment:

- Connect Cloudflare Workers Builds to the GitHub repo.
- Root directory: `apps/website`.
- Production branch: `master`.
- Build command: `npm ci && npm run build`.
- Deploy command: `npx wrangler deploy`.
- Preview deployments for pull requests.

Alternative:

- Use GitHub Actions with a Cloudflare API token.
- This is useful if repo-level CI should gate deploys before Cloudflare sees the build.

## Environment strategy

```text
local       wrangler dev / astro dev
preview     Cloudflare preview deployment per branch or PR
production  Cloudflare Worker on final custom domain
```

Potential domains:

```text
tokemetry.dev
tokemetry.io
tokemetry.com
```

Final domain is not decided in this plan.

## Implementation phases

### Phase W0 — Planning only

Current state.

Deliverables:

- `apps/website/PLAN.md`
- Brand assets under `assets/brand`

### Phase W1 — Static scaffold

Deliverables:

- Astro scaffold under `apps/website`.
- `package.json`, `astro.config.mjs`, `wrangler.jsonc`.
- Brand asset sync script.
- Header/footer/layout.
- Empty page routes.
- Local `npm run build` passes.

Acceptance:

- `npm run dev` works.
- `npm run build` generates `dist`.
- `wrangler dev` serves the built site or Worker-integrated preview.

### Phase W2 — Landing page content

Deliverables:

- Hero.
- Problem/solution sections.
- Feature cards.
- Privacy section.
- Architecture diagram.
- Roadmap section.
- GitHub CTA.

Acceptance:

- Landing page explains tokemetry in under 30 seconds.
- No misleading provider claims.
- No real private usage data shown.

### Phase W3 — Documentation pages

Deliverables:

- `/docs`
- `/docs/architecture`
- `/docs/security`
- `/docs/deployment`
- `/docs/api`

Acceptance:

- Docs mirror current PRD/design intent.
- Pages are concise, linkable, and technically precise.

### Phase W4 — Cloudflare deployment

Deliverables:

- Worker Static Assets deployment.
- Preview branch deployments.
- Custom domain setup.
- Security headers.
- Cache policy.

Acceptance:

- Production deploy runs from repository.
- Pull requests get preview URLs.
- Static assets cache correctly.

### Phase W5 — Polish and launch

Deliverables:

- SEO metadata.
- `sitemap.xml`.
- `robots.txt`.
- Open Graph image.
- Lighthouse pass.
- Accessibility pass.
- Final README link from root project README.

Acceptance:

- Site is launchable as public project homepage.
- Root README links to the website after deployment.

## Future integrations

Potential later features:

- Live public demo with fake data only.
- Release notes generated from GitHub releases.
- Contributor guide pages.
- Adapter development guide.
- Architecture decision records.
- Interactive cost/usage estimator.
- Hosted documentation generated from OpenAPI schema.

## Open decisions

1. Final domain.
2. Whether to use Astro or Vite-only static site. Recommendation: Astro.
3. Whether to use Cloudflare Git integration or GitHub Actions. Recommendation: Cloudflare Git integration first.
4. Whether to add analytics. Recommendation: defer until launch.
5. Whether root README should embed the horizontal SVG. Recommendation: yes after brand PR is merged.
6. Whether final wordmark should be outlined in SVG. Recommendation: yes before formal launch.

## Non-goals for v1

- No account system.
- No hosted tokemetry SaaS.
- No live user data.
- No server API proxying from the marketing website.
- No CMS.
- No blog engine until the landing/docs pages are useful.

## Build quality gates

Website changes should eventually pass:

- TypeScript check.
- Astro check.
- ESLint.
- Prettier.
- Build.
- Lighthouse performance/accessibility baseline.
- Link check for internal docs.

## Summary

The website should be a fast, static-first, Cloudflare-deployed technical product site. Keep it in the monorepo, keep `assets/brand` as the canonical brand source, and use `apps/website` as the implementation boundary. The first useful version should ship a sharp landing page, concise architecture/security docs, and a clear path to GitHub and self-hosting.
