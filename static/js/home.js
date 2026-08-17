/* ============================================================
   home.js  —  Homepage interactivity
   1. Hero banner: centered peek carousel (prev/next slide in)
   2. Auto-shifting movie carousels (pause on hover, arrows)
   3. Hover-to-play muted trailers on movie cards
   ============================================================ */

document.addEventListener('DOMContentLoaded', function () {

  /* ---------- 1. HERO BANNER (peek carousel, infinite loop) ---------- */
  (function initBanner() {
    var slider = document.querySelector('.banner-slider');
    if (!slider) return;
    var viewport = slider.querySelector('.banner-viewport');
    var track = slider.querySelector('.banner-track');
    var real = Array.prototype.slice.call(track.children);
    if (real.length === 0) return;

    var BANNER_MS = 4500;
    var EASE = 'transform 0.6s cubic-bezier(0.25, 0.8, 0.25, 1)';

    // clone last -> front and first -> end for a seamless loop
    var firstClone = real[0].cloneNode(true);
    var lastClone = real[real.length - 1].cloneNode(true);
    track.insertBefore(lastClone, real[0]);
    track.appendChild(firstClone);

    var slides = Array.prototype.slice.call(track.children);
    var index = 1;              // first real slide (after the leading clone)
    var timer = null;
    var jumping = false;

    function center() {
      var slide = slides[index];
      var offset = (viewport.clientWidth - slide.offsetWidth) / 2;
      track.style.transform = 'translateX(' + (offset - slide.offsetLeft) + 'px)';
      slides.forEach(function (s, i) { s.classList.toggle('active', i === index); });
    }

    function go(i) {
      track.style.transition = EASE;
      index = i;
      center();
    }

    // after sliding onto a clone, snap (without animation) to the real slide
    track.addEventListener('transitionend', function () {
      if (index === slides.length - 1) {          // trailing clone (= first)
        jumping = true; track.style.transition = 'none'; index = 1; center();
      } else if (index === 0) {                    // leading clone (= last)
        jumping = true; track.style.transition = 'none'; index = slides.length - 2; center();
      }
      if (jumping) {
        // force reflow so the next move animates again
        void track.offsetHeight; jumping = false;
      }
    });

    function next() { go(index + 1); }
    function prev() { go(index - 1); }

    function start() { if (!timer) timer = setInterval(next, BANNER_MS); }
    function stop() { clearInterval(timer); timer = null; }

    var prevBtn = slider.querySelector('.banner-nav.prev');
    var nextBtn = slider.querySelector('.banner-nav.next');
    if (prevBtn) prevBtn.addEventListener('click', prev);
    if (nextBtn) nextBtn.addEventListener('click', next);

    slider.addEventListener('mouseenter', stop);
    slider.addEventListener('mouseleave', start);
    window.addEventListener('resize', function () {
      track.style.transition = 'none'; center();
    });

    center();
    start();
  })();

  /* ---------- 2. MOVIE CAROUSELS ---------- */
  var AUTO_SHIFT_MS = 8000;

  document.querySelectorAll('.movie-carousel').forEach(function (carousel) {
    var viewport = carousel.querySelector('.movie-viewport');
    var track = carousel.querySelector('.movie-track');
    var cards = Array.prototype.slice.call(track.children);
    var prevBtn = carousel.querySelector('.carousel-nav.prev');
    var nextBtn = carousel.querySelector('.carousel-nav.next');
    if (cards.length === 0) return;

    var index = 0;
    var timer = null;

    function step() {
      if (cards.length > 1) return cards[1].offsetLeft - cards[0].offsetLeft;
      return cards[0].offsetWidth;
    }
    function pageSize() { return Math.max(1, Math.floor(viewport.clientWidth / step())); }
    function maxScroll() { return Math.max(0, track.scrollWidth - viewport.clientWidth); }
    function overflows() { return maxScroll() > 5; }

    function goTo(i) {
      if (i < 0) i = 0;
      if (i >= cards.length) i = 0;
      index = i;
      var target = cards[index].offsetLeft;
      if (target > maxScroll()) target = maxScroll();
      track.style.transform = 'translateX(' + (-target) + 'px)';
    }
    function next() {
      if (cards[index].offsetLeft >= maxScroll() - 2) goTo(0);
      else goTo(index + pageSize());
    }
    function prev() {
      if (index <= 0) goTo(cards.length - 1);
      else goTo(index - pageSize());
    }
    function start() { if (!timer && overflows()) timer = setInterval(next, AUTO_SHIFT_MS); }
    function stop() { clearInterval(timer); timer = null; }

    if (prevBtn) prevBtn.addEventListener('click', prev);
    if (nextBtn) nextBtn.addEventListener('click', next);
    carousel.addEventListener('mouseenter', stop);
    carousel.addEventListener('mouseleave', start);

    function updateArrows() {
      var show = overflows();
      if (prevBtn) prevBtn.style.display = show ? '' : 'none';
      if (nextBtn) nextBtn.style.display = show ? '' : 'none';
    }
    window.addEventListener('resize', function () { goTo(0); updateArrows(); });

    updateArrows();
    start();
  });

  /* ---------- 3. HOVER-TO-PLAY TRAILERS ---------- */
  var HOVER_DELAY_MS = 800;

  document.querySelectorAll('.movie-card').forEach(function (card) {
    var embed = card.getAttribute('data-embed');
    if (!embed) return; // no trailer -> just the CSS zoom effect

    var wrap = card.querySelector('.poster-wrap');
    var timer = null;

    card.addEventListener('mouseenter', function () {
      timer = setTimeout(function () {
        if (wrap.querySelector('iframe')) return;
        var iframe = document.createElement('iframe');
        iframe.className = 'trailer-frame';
        iframe.src = embed + '?autoplay=1&mute=1&controls=0&modestbranding=1&rel=0&playsinline=1';
        iframe.setAttribute('frameborder', '0');
        iframe.setAttribute('allow', 'autoplay; encrypted-media');
        wrap.appendChild(iframe);
      }, HOVER_DELAY_MS);
    });

    card.addEventListener('mouseleave', function () {
      clearTimeout(timer);
      var iframe = wrap.querySelector('iframe');
      if (iframe) iframe.remove();
    });
  });

});
