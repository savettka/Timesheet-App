(function () {
  "use strict";

  // ---------------------------------------------------------- theme toggle
  function initTheme() {
    var toggle = document.getElementById("theme-toggle");
    if (!toggle) return;
    toggle.addEventListener("click", function () {
      var root = document.documentElement;
      var current = root.getAttribute("data-theme") === "dark" ? "dark" : "light";
      var next = current === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      localStorage.setItem("stm-theme", next);
    });
  }

  // ---------------------------------------------------------- mobile nav
  function initHamburger() {
    var btn = document.getElementById("hamburger");
    var sidebar = document.querySelector(".sidebar");
    if (!btn || !sidebar) return;
    btn.addEventListener("click", function () {
      sidebar.classList.toggle("open");
    });
    document.addEventListener("click", function (e) {
      if (sidebar.classList.contains("open") && !sidebar.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
        sidebar.classList.remove("open");
      }
    });
  }

  // ---------------------------------------------------------- live clock
  function fmtHours(hoursDecimal) {
    var sign = hoursDecimal < 0 ? "-" : "";
    var abs = Math.abs(hoursDecimal);
    var totalMinutes = Math.round(abs * 60);
    var h = Math.floor(totalMinutes / 60);
    var m = totalMinutes % 60;
    return sign + h + "h " + String(m).padStart(2, "0") + "m";
  }

  function initLiveTimer() {
    var card = document.getElementById("clock-card");
    var valueEl = document.getElementById("live-timer-value");
    if (!card || !valueEl) return;

    var loggedIn = card.dataset.loggedIn === "true";
    if (!loggedIn) return;

    var onBreak = card.dataset.onBreak === "true";
    var loginIso = card.dataset.loginIso;
    var breakStartIso = card.dataset.breakStartIso;
    var closedBreakSeconds = parseInt(card.dataset.closedBreakSeconds || "0", 10);

    if (!loginIso) return;
    var loginMs = new Date(loginIso).getTime();
    var breakStartMs = breakStartIso ? new Date(breakStartIso).getTime() : null;

    function tick() {
      var now = Date.now();
      var rawSeconds = (now - loginMs) / 1000;
      var breakSeconds = closedBreakSeconds;
      if (onBreak && breakStartMs) {
        breakSeconds += (now - breakStartMs) / 1000;
      }
      var netHours = Math.max(0, (rawSeconds - breakSeconds) / 3600);
      valueEl.textContent = fmtHours(netHours);
    }

    tick();
    setInterval(tick, 1000);
  }

  // ---------------------------------------------------------- status poll
  function initStatusPoll() {
    if (!window.STM_STATUS_URL) return;

    function refresh() {
      fetch(window.STM_STATUS_URL, { headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data) return;

          var fill = document.getElementById("weekly-progress-fill");
          if (fill) fill.style.width = Math.min(100, data.weekly_progress_pct) + "%";

          var workedFig = document.getElementById("weekly-worked-fig");
          if (workedFig) workedFig.innerHTML = "<strong>" + data.weekly_worked_fmt + "</strong> worked";

          var banner = document.getElementById("weekly-banner");
          if (banner) {
            if (data.weekly_complete) {
              banner.className = "banner banner-success";
              banner.textContent = "🎉 You've hit your weekly target" + (data.is_logged_in ? " — you can log out any time." : ".");
            } else if (data.suggestion && !data.suggestion.reached) {
              banner.className = "banner banner-info";
              banner.innerHTML = data.weekly_remaining_fmt + " left this week. At this pace, punch out around <strong>" + data.suggestion.suggested_time + "</strong> today to hit your target.";
            } else {
              banner.className = "banner banner-muted";
              banner.textContent = data.weekly_remaining_fmt + " left to reach your weekly target.";
            }
          }
        })
        .catch(function () { /* silent - keep last known state */ });
    }

    // Only poll while the tab is actually being looked at. A dashboard left
    // open in a background tab all day would otherwise keep hitting the
    // server every 30s for a screen nobody is reading -- wasted requests,
    // and wasted CPU allowance on a small host.
    setInterval(function () {
      if (!document.hidden) refresh();
    }, 30000);

    // Coming back to the tab shouldn't mean waiting up to 30s for fresh numbers.
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) refresh();
    });
  }

  // ---------------------------------------------------------- break rows
  function initBreakRows() {
    var container = document.getElementById("break-rows");
    var addBtn = document.getElementById("add-break");
    var template = document.getElementById("break-row-template");
    if (!container || !addBtn || !template) return;

    addBtn.addEventListener("click", function () {
      var clone = template.content.cloneNode(true);
      container.appendChild(clone);
    });

    container.addEventListener("click", function (e) {
      var btn = e.target.closest(".remove-break");
      if (!btn) return;
      var rows = container.querySelectorAll(".break-row-input");
      if (rows.length <= 1) {
        btn.closest(".break-row-input").querySelectorAll("input").forEach(function (i) { i.value = ""; });
        return;
      }
      btn.closest(".break-row-input").remove();
    });
  }

  // ------------------------------------------------- profile picture picker
  function initAvatarPicker() {
    var input = document.getElementById("avatar-input");
    var label = document.getElementById("avatar-filename");
    if (!input || !label) return;

    var original = label.textContent;
    input.addEventListener("change", function () {
      if (!input.files || !input.files.length) {
        label.textContent = original;
        return;
      }
      // Confirm the pick, since the file dialog gives no other feedback
      // until the form is actually saved.
      label.textContent = input.files[0].name + " — press Save profile";
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initTheme();
    initHamburger();
    initLiveTimer();
    initStatusPoll();
    initBreakRows();
    initAvatarPicker();
  });
})();
