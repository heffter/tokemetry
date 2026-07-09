# tokemetry brand assets

Generated logo assets based on the selected tokemetry logo direction.

## Production SVGs
- `tokemetry-logo-horizontal-dark.svg` - primary horizontal lockup for dark backgrounds
- `tokemetry-logo-horizontal-light.svg` - horizontal lockup for light backgrounds
- `tokemetry-logo-vertical-dark.svg` - vertical lockup for dark backgrounds
- `tokemetry-logo-vertical-light.svg` - vertical lockup for light backgrounds
- `tokemetry-logo-monochrome-dark.svg` - monochrome vertical lockup for dark backgrounds
- `tokemetry-icon-dark.svg` - standalone icon for dark backgrounds
- `tokemetry-icon-light.svg` - standalone icon for light backgrounds
- `favicon.svg` - simplified icon-only favicon

## PNG exports
PNG exports are intentionally not committed as source assets. Generate them from the SVGs during release/website build, or use the local ZIP artifact from the design session.

## Applied refinements
- Horizontal lockup spacing tightened.
- Icon scaled down slightly relative to the wordmark.
- Token cluster normalized to exactly three hexagons.
- Large left token reduced for better optical balance.
- Bracket treated as a usage-limit marker.
- Sparkline softened toward analytics/burn-rate telemetry rather than ECG.
- Decorative wordmark hexagons removed from lockups to keep the text unobstructed at small sizes.
- Dark-background frames brightened for better contrast in compact UI contexts.
- Favicon simplified and centered inside the viewBox to avoid clipping at browser icon sizes.
- Wordmarks outlined from Segoe UI Semibold to avoid installed-font rendering differences.

Note: The outlined wordmark is based on Segoe UI Semibold, the Windows font from the original SVG fallback stack.
