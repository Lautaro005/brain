---
name: brain
description: One memory for every AI you use, filed as a slip-box of typed index cards.
colors:
  ink: "#0a0a0a"
  ink-2: "#3d3d3d"
  muted: "#5e5e5e"
  line: "#b4b4b0"
  rule: "#cfcfcc"
  rule-soft: "#e3e3e0"
  desk: "#ececea"
  card: "#ffffff"
typography:
  display:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "clamp(2.9rem, 6.4vw, 5.6rem)"
    fontWeight: 800
    lineHeight: 0.93
    letterSpacing: "-0.035em"
    fontVariation: "'wdth' 84"
  headline:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "clamp(2.1rem, 4.2vw, 3.5rem)"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "-0.03em"
    fontVariation: "'wdth' 86"
  title:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.55
  typed:
    fontFamily: "Courier Prime, Courier New, monospace"
    fontSize: "14.5px"
    fontWeight: 400
    lineHeight: "26px"
  label:
    fontFamily: "Courier Prime, Courier New, monospace"
    fontSize: "11.5px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "0.06em"
rounded:
  card: "3px"
  panel: "4px"
  tab: "6px"
spacing:
  card-x: "14px"
  gutter: "clamp(16px, 4vw, 40px)"
  section: "clamp(72px, 9vw, 128px)"
  max: "1240px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.card}"
    rounded: "{rounded.card}"
    padding: "0 18px"
    height: "46px"
  button-primary-hover:
    backgroundColor: "#2a2a2a"
  button-outline:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "0 18px"
    height: "46px"
  button-outline-hover:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.card}"
  button-copy:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.card}"
    padding: "0 11px"
    height: "32px"
  index-card:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    typography: "{typography.typed}"
    rounded: "{rounded.card}"
    padding: "10px 14px 12px"
  stamp:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.card}"
    padding: "2px 8px 1px"
  stamp-solid:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.card}"
    rounded: "{rounded.card}"
    padding: "2px 8px 1px"
  nav-tab:
    backgroundColor: "#f6f6f4"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.tab}"
    padding: "9px 13px 10px"
  nav-tab-active:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
---

# Design System: brain

## Overview

**Creative North Star: "The Working Slip-Box"**

The page is a Zettelkasten drawer on a grey desk. Every piece of content is a white index card with a black ruled header, a reference number in the top-right corner, and a typed body on faint ruled lines. Cards cross-reference each other with Luhmann-style numbers (0, 1, 1a, 1.2, 3.1), and those numbers are the system's navigation, numbering and wayfinding all at once: section dividers, drawer tabs and nav tabs all carry them.

The world is strictly monochrome: white card stock, black typewriter ink, and a small ladder of pencil greys for rules, reference lines and secondary text. Emphasis is made with ink weight and inversion (black header, white type), never with colour. Density is moderate and paper-like; sections breathe with large vertical padding while the cards themselves are compact and tightly typed.

Archivo carries the voice (condensed, heavy display; plain body). Courier Prime appears only where something is literally typed onto a card or entered in a terminal: card bodies, card headers, reference numbers, stamps, the install command.

This system governs the landing page (`docs/index.html`). The product dashboard (`brain_mcp/dashboard.html`) predates it, has its own look, and is out of this system's scope; the landing's dashboard mock is a recreation of that product UI, not a rule for it.

**Key Characteristics:**
- Index card as the atom: white, 1px grey border, 3px corners, black-ruled header, reference number top-right.
- Monochrome only: ink, greys, card white, desk grey.
- Reference numbers everywhere a real slip-box would have them.
- Drawer tabs (top-rounded, staggered, lifted) as navigation and section markers.
- Rubber-stamp outlined caps for status.
- Pencil-thin dashed reference lines between cards; they ink solid on interaction.

## Colors

A monochrome ink-on-card-stock palette: one true black, two text greys, three pencil greys for lines, and two paper surfaces.

### Primary
- **Typewriter Ink** (`ink`): all primary text, card header rules, active tab and drawer-body borders, solid stamps, primary buttons, the inverted closing section and footer, focus outlines, and the inked state of reference lines.

### Neutral
- **Carbon Grey** (`ink-2`): secondary prose (ledes, section intros, panel paragraphs, step copy).
- **Pencil Grey** (`muted`): reference numbers, "reads" labels, sample-data captions, meta lines, terminal prompts. Minimum text grey; do not go lighter for text.
- **Reference Line Grey** (`line`): idle dashed reference lines and their anchor dots, dashed outlines of the "versus" frames. Non-text only.
- **Card Edge** (`rule`): card borders, section top borders, header bottom border, inactive tab borders.
- **Ruled Line** (`rule-soft`): the faint horizontal lines inside card bodies and row dividers in the agent catalog.
- **Desk** (`desk`): page background; the surface cards sit on.
- **Card Stock** (`card`): every card, drawer body, catalog and active tab; also the inverse text colour on ink.

### Named Rules
**The Ink-Only Rule.** No hue enters the page. Emphasis is ink weight, inversion (ink fill, card-white text) or the solid reference line; never a colour accent. This is a pinned brand commitment for the landing.

**The Pencil Floor Rule.** Text never goes lighter than Pencil Grey on card or desk. The two lightest greys (`line`, `rule-soft`) draw lines, never words.

## Typography

**Display Font:** Archivo (with Helvetica Neue, Arial)
**Body Font:** Archivo (with Helvetica Neue, Arial)
**Label/Mono Font:** Courier Prime (with Courier New)

**Character:** A heavy, condensed grotesque doing the talking over a warm typewriter face doing the filing. Archivo's width axis is used (84-88% on display, 80% on step numerals) to get tall, compact headlines without a second display family.

### Hierarchy
- **Display** (800, clamp(2.9rem, 6.4vw, 5.6rem), 0.93, width 84%, -0.035em, balanced): the hero headline only. A second line may drop to Pencil Grey for the counter-phrase.
- **Headline** (800, clamp(2.1rem, 4.2vw, 3.5rem), 1, width 86%, -0.03em): section titles.
- **Title** (700, 1.25rem, 1.2): panel and step titles.
- **Body** (400, 17px, 1.55): prose; ledes at clamp(1.05rem, 1.35vw, 1.2rem) capped near 34em, section intros at 1.13rem capped near 38em.
- **Typed** (Courier Prime 400, 14.5px on a 26px ruled line; 13px/22px on small agent cards, 12.5px/22px on silo cards): everything written on a card.
- **Label** (Courier Prime 700, 11-13px, 0.06-0.12em tracking, uppercase): card headers, stamps, copy button, catalog group rows, language switch.
- **Wordmark** (Archivo 800, 25px, width 88%, -0.03em): lowercase "brain" only.

### Named Rules
**The Typed-Only-When-Typed Rule.** Courier Prime appears only for what is literally typed on a card, entered in a terminal, or stamped: card bodies and headers, reference numbers, commands, file paths, stamps. Headlines, prose and buttons stay in Archivo.

**The Ruled Card Rule.** Card body line-height equals the ruled-line pitch (26px default, 22px on small cards) so typed text sits on the lines.

## Layout

A centred column capped at 1240px with a fluid gutter (clamp(16px, 4vw, 40px)). The hero is an asymmetric two-column grid (about 5.2 : 6.8) with the text column left and the card spread right; content panels use 5 : 7 and the privacy block 7 : 5. Sections are separated by a 1px Card Edge rule and generous vertical padding (clamp(72px, 9vw, 128px)); a divider card hangs off each section's top rule carrying the section's reference number.

The hero spread is absolutely positioned: the memory card centred, agent cards at the four corners, an SVG of dashed reference lines drawn between them and recalculated on resize.

Breakpoints: at 1020px the hero, panels, steps and privacy collapse to one column; at 760px the top nav tabs hide, the versus pair stacks, drawer tabs become a horizontal scroll strip and the catalog rows reflow to two columns; at 620px the spread becomes a grid (memory card on top, agents in two columns) with reference lines dropped in favour of the written "reads 1.x" references.

## Elevation & Depth

Paper on a desk: low, soft, physical shadows, never glows and never hard offsets. Depth comes mainly from surface contrast (white card on grey desk) and borders; shadow only confirms that a card is a loose object.

### Shadow Vocabulary
- **Resting card** (`box-shadow: 0 1px 0 rgba(10,10,10,.07), 0 3px 5px -2px rgba(10,10,10,.16)`): every card, the drawer body, the catalog.
- **Lifted card** (`box-shadow: 0 1px 0 rgba(10,10,10,.09), 0 9px 10px -6px rgba(10,10,10,.3)`): an agent card while active (paired with a 3px upward shift) and the dashboard window mock.
- **Divider tab** (`box-shadow: 0 2px 3px -2px rgba(10,10,10,.14)`): section divider cards hanging off a rule.

### Named Rules
**The Paper Shadow Rule.** Shadows are short, soft and downward, like card stock on a desk. Cards embedded inside another card (demo cards in the drawer) drop their shadow.

## Shapes

Nearly square paper. Cards, buttons, stamps and the copy button use 3px corners; framing containers (drawer body, catalog, versus frames) use 4px. Tabs are rounded only on the edge that sticks out of the drawer: nav tabs 6px on top, drawer tabs 7px on top, section divider cards 6px on the bottom. Borders are 1px for card edges and 1.5px for anything pressable or stamped (buttons, copy, stamps, language switch). Dashed strokes are native to the world: reference lines (3 4 dash), the "then" rule inside the install card, and the versus frames.

## Components

### Buttons
Ink-bordered and plain, like a label maker strip.
- **Shape:** gently squared (3px), 46px tall, 1.5px ink border, Archivo 600 15px, 18px horizontal padding, optional 18px inline SVG icon.
- **Primary (dark):** ink fill, card-white text; hover lightens to #2a2a2a.
- **Outline:** card-white fill, ink text; hover inverts to ink fill.
- **On ink (closing section):** transparent with a card-white border; hover inverts to card-white fill.
- **Active:** 1px downward press. Focus: 2px ink outline, 3px offset (global).

### Copy Button
Typed-label action attached to the install command: 32px tall, 1.5px ink border, Courier Prime 700 11.5px uppercase with 0.08em tracking, inline copy icon. Hover and the copied state both invert to ink.

### Index Card (signature)
- **Corner Style:** 3px.
- **Background:** Card Stock, 1px Card Edge border, resting shadow.
- **Header:** Courier Prime 700 uppercase label on the left, reference number (Courier Prime 400 13px, no caps) on the right, 1px ink rule underneath.
- **Body:** Courier Prime on faint Ruled Line stripes whose pitch equals the line-height. The install card and terminal cards drop the stripes.
- **Active (agent cards):** header inverts to ink with card-white text, card lifts 3px with the lifted shadow.

### Stamps
Rubber-stamp status marks: inline, 1.5px currentColor border, 3px corners, Courier Prime 700 11px uppercase with 0.12em tracking. Outline for pending or neutral status ("Connect", "One click"), solid ink for a positive state ("Connected", "Shared").

### Navigation
- **Top bar:** sticky, desk at 92% with blur, 1px bottom rule; lowercase wordmark with the card favicon left; GitHub link and EN/ES switch right.
- **Nav tabs:** drawer tabs sitting on the bar's bottom rule, each with a Courier reference number before the Archivo 600 14px label. Inactive tabs sit 3px low on off-white #f6f6f4; hover and the current section rise flush on card white, the current one with an ink border. Hidden under 760px.
- **Language switch:** two-segment typed toggle in a 1.5px ink frame; the pressed language is ink-filled.

### Drawer Tabs
Tablist for the contents drawer: card-white tabs with 7px top corners, numbered 3.1-3.5 in Courier, staggered (alternate tabs sit 10px low, others 6px) like real divider cards. The selected tab rises flush, gains an ink border and merges into the ink-bordered drawer body below. Scrolls horizontally on narrow screens.

### Section Divider Card
A small card hanging from each section's top rule, holding only the section's reference number in Courier Prime 700 13px; bottom corners 6px.

### Reference Lines
Dashed 1.1px Reference Line Grey paths with small hollow anchor dots joining agent cards to the memory card. On hover, focus or tap of an agent card, its line inks solid at 1.8px, its dot fills, and the memory entries it reads turn bold with a pencil underline and a drawn check mark.

### Agent Catalog
One ledger card listing agents as rows: Courier reference number (1a-1i), Archivo 700 name, Courier config path or note, and a stamp. Rows are divided by Ruled Line; group rows use an off-white #f5f5f3 band with an uppercase typed label.

### Motion
One ease for everything (cubic-bezier(.16, 1, .3, 1)). On load, the memory and agent cards file out of the drawer once (26px rise with a 1.2 degree alternating tilt, 900ms, 90ms stagger). State transitions are 150-250ms. With reduced motion, the file-out and the auto demo cycle are skipped and smooth scrolling is off.

## Do's and Don'ts

### Do:
- **Do** build every new content block as an index card: white, 1px Card Edge border, 3px corners, ink-ruled header, reference number top-right.
- **Do** number things the Luhmann way (1, 1a, 1.2, 3.1) and let the numbers carry navigation and cross-reference.
- **Do** set typed matter (card bodies, commands, paths, reference numbers, stamps) in Courier Prime and everything else in Archivo.
- **Do** show state by inversion: ink fill with card-white text for active headers, selected toggles, solid stamps and hovered buttons.
- **Do** keep product visuals as HTML/SVG recreations of the real UI, marked as sample data.
- **Do** keep shadows to the two paper shadows (resting, lifted).

### Don't:
- **Don't** introduce any hue; the landing is black and white by brand commitment.
- **Don't** set text in `line` or `rule-soft` greys; Pencil Grey (`muted`) is the lightest text.
- **Don't** use Courier Prime for headlines, prose or button labels.
- **Don't** use hard offset shadows or glows; depth is soft paper shadow and surface contrast.
- **Don't** round cards, buttons or stamps beyond 4px; only tab edges get 6-7px.
- **Don't** apply this system to the product dashboard (`brain_mcp/dashboard.html`); it is out of scope.
