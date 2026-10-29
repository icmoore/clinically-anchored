# Clinically Anchored: Color Schema

Brand identity · Revised 2026-10-29 (Sarah's sky blues, option A) · replaces the 2026-10-06 Harbor Teal schema

Light scheme only; dark mode is not defined. Structure follows the AnchorRegistry color schema (March 2026). The navy ink is shared with AnchorRegistry so the products read as siblings.

The CSS tokens at the bottom of this file are the source for `apps/web/src/app/globals.css`. Change a color here first, then there.

## Why it changed

Sarah chose three shades (email, 2026-10-25): `#3EC5F7`, `#BAEBFC`, `#E7F8FE`, light blues she prefers for patients under acute stress. `#3EC5F7` is too light for white text (2.0:1) or for text on white, so it can't do the old teal's job of button-with-white-text and link color. Option A (chosen 2026-10-29): sky-blue buttons with navy text, and a darker blue of the same hue for links, outline buttons and the focus ring.

## Brand colors

| Token | Name | HEX | Role |
|---|---|---|---|
| `ink` | Deep Navy | `#0C2340` | Text, dark bands, and the text on primary buttons. Shared with AnchorRegistry. 14.5:1 on canvas. |
| `primary` | Sky | `#3EC5F7` | Primary button fill, brand mark, highlights. Never used for text. Navy on it 7.9:1. |
| `primary-tint` | Pale Sky | `#BAEBFC` | Selected rows, highlighted areas, hero bands. Navy on it 12.3:1; `link` on it 4.8:1. |
| `canvas` | Mist | `#E7F8FE` | Page background. |
| `link` | Deep Sky | `#0F698A` | Links, outline-button text and borders, focus ring, any blue text. 6.2:1 on white, 5.7:1 on canvas. |

## Primary states

| Token | HEX | Role |
|---|---|---|
| `primary-hover` | `#1AACE3` | Button hover, navy text 6.1:1 |
| `primary-active` | `#139FD6` | Button pressed, navy text 5.2:1 |
| `on-primary` | `#0C2340` | Text and icons on primary fills (= ink) |
| `link-hover` | `#0D5B77` | Link and outline-button hover, 7.6:1 on white |
| `focus` | `#0F698A` | Focus ring (= link); 6.2:1 on white, 3.1:1 against a sky button |

## Neutrals (tints of ink; cool to match the blue canvas)

| Token | HEX | Role |
|---|---|---|
| `surface` | `#FFFFFF` | Cards, inputs |
| `surface-subtle` | `#EEF2F6` | Hover rows, table headers, section bands (was warm `#EEEDE8`) |
| `border` | `#D5DEE5` | Dividers, card edges, decorative (was `#DADBDA`) |
| `border-strong` | `#75818F` | Input outlines (4.0:1 on white, 3.6:1 on canvas) |
| `text-muted` | `#526275` | Metadata, help text (6.2:1 on white, 5.7:1 on canvas, 4.9:1 on primary-tint) |
| `text-disabled` | `#A4ABB2` | Disabled controls only |
| `ink-raised` | `#1C2B4A` | Cards on dark bands. Shared with AnchorRegistry. |

## Clinical status (functional; never used for branding)

Each status is a badge: text on background, with border, and a dot or icon. Solid colors are for dots and icons only.

| Status | Text | Background | Border | Solid | Text on bg |
|---|---|---|---|---|---|
| Red flag | `#B42318` | `#FDECEA` | `#F5C2BC` | `#D92D20` | 5.8:1 (unchanged) |
| Watch | `#92400E` | `#FEF3C7` | `#F6D68A` | `#D97706` | 6.4:1 (unchanged) |
| Clear | `#166534` | `#E7F4EB` | `#B9DEC5` | `#16A34A` | 6.3:1 (unchanged) |
| Info | `#1C2B4A` | `#EEF2F6` | `#CBD5E1` | `#64748B` | 12.5:1 (now neutral slate; the old blue Info blended into the sky brand and the canvas) |

Error messages (form validation, failed requests) and destructive actions such as Reject use the red-flag text and border colors, but never the status badge itself.

## AI marker (functional, unchanged)

| Text | Background | Border | Solid | Text on bg |
|---|---|---|---|---|
| `#5B21B6` | `#F1ECFD` | `#D9CCF7` | `#7C3AED` | 7.8:1 |

Used only on AI-generated content.

## Usage notes

- Sky (`primary`) is a fill, never text: buttons with navy text, the brand mark, highlights. Anything that must be read as blue text uses `link`.
- Outline (secondary) buttons use `link` for text and border, so the border meets 3:1 against white.
- Red, amber and green stay reserved for clinical status. Clear green is now well apart from the brand blue (the old teal-vs-green caveat no longer applies).
- Never rely on color alone: every status carries a word and an icon.
- AnchorRegistry's Gold appears only inside the AnchorRegistry mark on a receipt badge, when public anchoring ships.
- All text pairs above meet WCAG 2 AA (4.5:1) on their own background, on white and on the canvas.

## CSS tokens

```css
:root {
  --ink: #0C2340;
  --primary: #3EC5F7;
  --primary-tint: #BAEBFC;
  --canvas: #E7F8FE;
  --link: #0F698A;

  --primary-hover: #1AACE3;
  --primary-active: #139FD6;
  --on-primary: #0C2340;
  --link-hover: #0D5B77;
  --focus: #0F698A;

  --surface: #FFFFFF;
  --surface-subtle: #EEF2F6;
  --border: #D5DEE5;
  --border-strong: #75818F;
  --text-muted: #526275;
  --text-disabled: #A4ABB2;
  --ink-raised: #1C2B4A;

  --flag-text: #B42318;  --flag-bg: #FDECEA;  --flag-border: #F5C2BC;  --flag-solid: #D92D20;
  --watch-text: #92400E; --watch-bg: #FEF3C7; --watch-border: #F6D68A; --watch-solid: #D97706;
  --clear-text: #166534; --clear-bg: #E7F4EB; --clear-border: #B9DEC5; --clear-solid: #16A34A;
  --info-text: #1C2B4A;  --info-bg: #EEF2F6;  --info-border: #CBD5E1;  --info-solid: #64748B;

  --ai-text: #5B21B6;    --ai-bg: #F1ECFD;    --ai-border: #D9CCF7;    --ai-solid: #7C3AED;
}
```
