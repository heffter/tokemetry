# tokemetry website

Static-first public website for tokemetry.

## Development

```bash
npm install
npm run dev
```

The site keeps `assets/brand` as the canonical brand source. `npm run sync:brand`
copies the selected SVGs into `public/brand` and `public/favicon.svg` before dev
and build commands.

## Build

```bash
npm run check
npm run build
```

## Cloudflare

`wrangler.jsonc` targets Cloudflare Workers Static Assets with `src/worker.ts`
adding security headers around static asset responses.
