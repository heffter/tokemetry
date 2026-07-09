# tokemetry dashboard

Vue 3 + TypeScript + ECharts single-page application for the tokemetry
server.

## Views

| Route | View |
|---|---|
| `/` | **Now** — live limit gauges, burn rate, predicted exhaustion, today by model, live activity feed (WebSocket) |
| `/trends` | daily token trend with a model/machine/project dimension toggle |
| `/blocks` | 5-hour block timeline and table |
| `/breakdowns` | model / machine / project breakdowns and cache efficiency |
| `/sessions` | recent sessions table |
| `/machines` | fleet health and staleness |
| `/settings` | theme, API token management, pricing table |

## Development

```
npm install
npm run dev        # dev server, proxies /api and the WebSocket to :8787
npm run build      # type-check (vue-tsc --noEmit) + production build
npm run test       # vitest
npm run lint       # eslint + prettier --check
```

The API bearer token is entered in the app's connect gate and stored in
local storage; it is sent as `Authorization: Bearer <token>` on every call.
The theme (system/light/dark) is persisted the same way.

## Design

Colors follow the validated dataviz reference palette (`src/lib/palette.ts`):
categorical hues are assigned in fixed order and never cycled, status is
carried by color plus a visible label (never color alone), and light/dark are
separately selected step sets rather than an automatic inversion. Theme
tokens live in `src/styles/theme.css` and are consumed by role.
