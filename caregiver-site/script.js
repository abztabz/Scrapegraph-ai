/* =========================================================
   Gentle Hands Care — script.js
   · Mobile nav toggle
   · Smooth-scroll for in-page anchors (+ close mobile nav)
   · IntersectionObserver fade-in for .reveal elements
   · Simple, client-side contact form handling
   · Auto-updates the footer year
   ========================================================= */
(function () {
  "use strict";

  /* ---------------------------------------------------------
     Helpers
  --------------------------------------------------------- */
  const prefersReducedMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)"
  ).matches;

  /* ---------------------------------------------------------
     Mobile navigation toggle
  --------------------------------------------------------- */
  const navToggle = document.querySelector(".nav-toggle");
  const nav = document.getElementById("primary-nav");

  function closeNav() {
    if (!nav || !navToggle) return;
    nav.classList.remove("is-open");
    navToggle.setAttribute("aria-expanded", "false");
  }

  if (navToggle && nav) {
    navToggle.addEventListener("click", function () {
      const isOpen = nav.classList.toggle("is-open");
      navToggle.setAttribute("aria-expanded", String(isOpen));
    });

    // Close when a nav link is chosen
    nav.addEventListener("click", function (event) {
      if (event.target.closest("a")) closeNav();
    });

    // Close on Escape
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeNav();
    });
  }

  /* ---------------------------------------------------------
     Smooth-scroll for same-page anchors
     (respects reduced-motion; native CSS handles the rest)
  --------------------------------------------------------- */
  document.querySelectorAll('a[href^="#"]').forEach(function (link) {
    link.addEventListener("click", function (event) {
      const targetId = link.getAttribute("href");
      if (!targetId || targetId === "#") return;

      const target = document.querySelector(targetId);
      if (!target) return;

      event.preventDefault();
      target.scrollIntoView({
        behavior: prefersReducedMotion ? "auto" : "smooth",
        block: "start",
      });

      // Move focus for accessibility without an extra visible jump
      target.setAttribute("tabindex", "-1");
      target.focus({ preventScroll: true });
    });
  });

  /* ---------------------------------------------------------
     IntersectionObserver fade-in
  --------------------------------------------------------- */
  const revealEls = document.querySelectorAll(".reveal");

  if (prefersReducedMotion || !("IntersectionObserver" in window)) {
    // Show everything immediately as a graceful fallback
    revealEls.forEach((el) => el.classList.add("is-visible"));
  } else {
    const observer = new IntersectionObserver(
      function (entries, obs) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            obs.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.15, rootMargin: "0px 0px -40px 0px" }
    );

    revealEls.forEach((el) => observer.observe(el));
  }

  /* ---------------------------------------------------------
     Contact form — simple client-side handling
     (no backend; replace with a real endpoint when ready)
  --------------------------------------------------------- */
  const form = document.getElementById("contact-form");
  const status = document.getElementById("form-status");

  function setStatus(message, type) {
    if (!status) return;
    status.textContent = message;
    status.classList.remove("is-success", "is-error");
    if (type) status.classList.add("is-" + type);
  }

  if (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();

      // Use the browser's built-in validation first
      if (!form.checkValidity()) {
        form.reportValidity();
        setStatus("Please fill in the required fields.", "error");
        return;
      }

      const name = (form.elements["name"]?.value || "").trim();

      // Simulate an async submission
      const submitBtn = form.querySelector('button[type="submit"]');
      if (submitBtn) submitBtn.disabled = true;
      setStatus("Sending…", null);

      window.setTimeout(function () {
        form.reset();
        if (submitBtn) submitBtn.disabled = false;
        setStatus(
          `Thank you${name ? ", " + name : ""}! We'll be in touch within one business day.`,
          "success"
        );
      }, 700);
    });
  }

  /* ---------------------------------------------------------
     Footer year
  --------------------------------------------------------- */
  const yearEl = document.getElementById("year");
  if (yearEl) yearEl.textContent = String(new Date().getFullYear());
})();
