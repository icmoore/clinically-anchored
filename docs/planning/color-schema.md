# Clinically Anchored: Color Schema

Brand identity · October 2026 · Locked (2026-10-06)

Light scheme only for now; dark mode is not defined. Structure follows the AnchorRegistry color schema (March 2026). Colors deliberately differ from AnchorRegistry's, except for two shared navies that mark the products as siblings.

The CSS tokens at the bottom of this file are the source for `apps/web/src/app/globals.css`. Change a color here first, then there.

## Brand colors (3)

| Token | Name | HEX | Role |
|---|---|---|---|
| `ink` | Deep Navy | `#0C2340` | Text, dark bands. Shared with AnchorRegistry (its Deep Navy). |
| `primary` | Harbor Teal | `#0E7C7B` | Buttons, links, brand mark, focus ring. White text on it 5.0:1. |
| `canvas` | Warm Off-White | `#F6F4EF` | Page background. Ink on it 14.4:1; primary on it 4.6:1. |

## Primary states

| Token | HEX | Role |
|---|---|---|
| `primary-hover` | `#0C6969` | Hover (white text 6.5:1) |
| `primary-active` | `#0A5D5C` | Pressed (white text 7.7:1) |
| `primary-tint` | `#ECF5F4` | Selected row, subtle highlight (primary text on it 4.5:1) |

## Neutrals (tints of ink; no new hues)

| Token | HEX | Role |
|---|---|---|
| `surface` | `#FFFFFF` | Cards, inputs |
| `surface-subtle` | `#EEEDE8` | Hover rows, table headers, section bands |
| `border` | `#DADBDA` | Dividers, card edges (decorative) |
| `border-strong` | `#75818F` | Input outlines (4.0:1 on white, meets 3:1 for UI components) |
| `text-muted` | `#526275` | Metadata, help text, placeholders (5.7:1 on canvas, 6.2:1 on white) |
| `text-disabled` | `#A4ABB2` | Disabled controls only (exempt from contrast) |
| `ink-raised` | `#1C2B4A` | Cards on dark bands. Shared with AnchorRegistry (its Surface). |

## Clinical status (functional; never used for branding)

Each status is a badge built from all four values: text on background, with border, and a solid dot or icon. Solid colors are for icons and dots only, never for text.

| Status | Text | Background | Border | Solid | Text on bg |
|---|---|---|---|---|---|
| Red flag | `#B42318` | `#FDECEA` | `#F5C2BC` | `#D92D20` | 5.8:1 |
| Watch | `#92400E` | `#FEF3C7` | `#F6D68A` | `#D97706` | 6.4:1 |
| Clear | `#166534` | `#E7F4EB` | `#B9DEC5` | `#16A34A` | 6.3:1 |
| Info | `#1E4FAF` | `#EAF0FC` | `#C4D4F5` | `#2563EB` | 6.6:1 |

Error messages (form validation, failed requests) and destructive actions such as Reject use the red-flag text and border colors, but never the status badge itself.

## AI marker (functional)

| Text | Background | Border | Solid | Text on bg |
|---|---|---|---|---|
| `#5B21B6` | `#F1ECFD` | `#D9CCF7` | `#7C3AED` | 7.8:1 |

Used only on AI-generated content: the "AI draft" label, the left rule on draft text, AI summaries. Replaces the earlier light-blue AI badge, which read as "info".

## Usage notes

- Three brand colors, by decision. Teal does both mark and UI work, so no fourth brand accent is needed. Extra hues exist only where they carry meaning (status, AI).
- Red, amber and green are reserved for clinical status. The brand never uses them, so a flag is never mistaken for decoration.
- Never rely on color alone: every status also carries a word ("Red flag", "Watch", "Clear") and an icon or dot.
- Clear green (hue 143°) and the teal primary (hue 179°) are related; they stay distinct because clear only appears as a labeled status badge, never as a button or link. Fallback if they ever clash in practice: clear becomes neutral grey with a check icon.
- AnchorRegistry's Gold (`#F59E0B`) is not a UI color here; it is the same family as the Watch amber. It appears only inside the AnchorRegistry mark on a receipt badge, if and when public anchoring ships, as an "Anchored by AnchorRegistry" logo.
- AnchorRegistry's Electric Blue was not adopted (white text on it is 3.7:1, below AA for buttons), nor its dark-first canvas: the clinical app is light by default.
- All text pairs above meet WCAG 2 AA (4.5:1) on their own background, on white, and on the canvas.

## CSS tokens

```css
:root {
  --ink: #0C2340;
  --primary: #0E7C7B;
  --canvas: #F6F4EF;

  --primary-hover: #0C6969;
  --primary-active: #0A5D5C;
  --primary-tint: #ECF5F4;

  --surface: #FFFFFF;
  --surface-subtle: #EEEDE8;
  --border: #DADBDA;
  --border-strong: #75818F;
  --text-muted: #526275;
  --text-disabled: #A4ABB2;
  --ink-raised: #1C2B4A;

  --flag-text: #B42318;  --flag-bg: #FDECEA;  --flag-border: #F5C2BC;  --flag-solid: #D92D20;
  --watch-text: #92400E; --watch-bg: #FEF3C7; --watch-border: #F6D68A; --watch-solid: #D97706;
  --clear-text: #166534; --clear-bg: #E7F4EB; --clear-border: #B9DEC5; --clear-solid: #16A34A;
  --info-text: #1E4FAF;  --info-bg: #EAF0FC;  --info-border: #C4D4F5;  --info-solid: #2563EB;

  --ai-text: #5B21B6;    --ai-bg: #F1ECFD;    --ai-border: #D9CCF7;    --ai-solid: #7C3AED;
}
```
