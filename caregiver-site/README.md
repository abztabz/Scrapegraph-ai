# Gentle Hands Care — Caregiver Site

A lightweight, dependency-free marketing site for an in-home caregiving service.
Built with plain HTML, CSS, and JavaScript so it can be hosted anywhere static
files are served (GitHub Pages, Netlify, S3, etc.).

## Structure

```
caregiver-site/
├─ index.html        # markup
├─ styles.css        # all styling (mobile-first, CSS variables, subtle animations)
├─ script.js         # smooth-scroll, form handling, IntersectionObserver fade-in
└─ assets/
   ├─ hero-bg.jpg    # hero background (placeholder gradient — replace with a 1920×1080 photo)
   ├─ logo.svg       # brand logo
   └─ favicon.ico    # site favicon
```

## Features

- **Mobile-first, responsive** layout using CSS Grid and custom properties.
- **Sticky header** with an accessible hamburger menu on small screens.
- **Smooth in-page scrolling** with focus management for keyboard users.
- **Scroll-reveal animations** via `IntersectionObserver` (gracefully degrades and
  respects `prefers-reduced-motion`).
- **Client-side contact form** with built-in validation and status messaging.
  No backend is wired up — point the submit handler at your endpoint when ready.

## Customizing

- **Brand & colors:** edit the CSS custom properties at the top of `styles.css`
  (`:root`).
- **Hero photo:** replace `assets/hero-bg.jpg` with your own high-res image
  (1920×1080 recommended). The current file is a generated gradient placeholder.
- **Logo / favicon:** swap `assets/logo.svg` and `assets/favicon.ico`.
- **Copy:** all text lives in `index.html`.

## Running locally

Open `index.html` directly, or serve the folder:

```bash
cd caregiver-site
python3 -m http.server 8000
# visit http://localhost:8000
```
