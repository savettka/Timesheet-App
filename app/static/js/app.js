(function () {
  "use strict";

  // ------------------------------------------------------------- helpers
  function fmtHours(hours) {
    var total = Math.max(0, Math.round(hours * 60));
    return Math.floor(total / 60) + "h " + String(total % 60).padStart(2, "0") + "m";
  }

  // A length of time for a sentence: 25m, 1h 05m, 8h (matches the server).
  function fmtDuration(hours) {
    var total = Math.max(0, Math.round(hours * 60));
    var h = Math.floor(total / 60);
    var m = total % 60;
    if (!h) return m + "m";
    if (!m) return h + "h";
    return h + "h " + String(m).padStart(2, "0") + "m";
  }

  // ---------------------------------------------------------------- theme
  function currentTheme() {
    var set = document.documentElement.getAttribute("data-theme");
    if (set === "dark" || set === "light") return set;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark" : "light";
  }

  function initTheme() {
    var toggles = document.querySelectorAll(".js-theme-toggle");
    if (!toggles.length) return;

    function sync() {
      var dark = currentTheme() === "dark";
      toggles.forEach(function (t) { t.setAttribute("aria-pressed", dark ? "true" : "false"); });
    }

    toggles.forEach(function (toggle) {
      toggle.addEventListener("click", function () {
        var next = currentTheme() === "dark" ? "light" : "dark";
        document.documentElement.setAttribute("data-theme", next);
        try { localStorage.setItem("stm-theme", next); } catch (e) { /* not saved - still applied */ }
        sync();
      });
    });

    if (window.matchMedia) {
      var mq = window.matchMedia("(prefers-color-scheme: dark)");
      if (mq.addEventListener) mq.addEventListener("change", sync);
    }
    sync();
  }

  // ------------------------------------------------------------- messages
  function initFlashes() {
    document.addEventListener("click", function (e) {
      var btn = e.target.closest(".flash-close");
      if (!btn) return;
      var flash = btn.closest(".flash");
      var stack = flash && flash.parentElement;
      if (flash) flash.remove();
      if (stack && !stack.children.length) stack.remove();
      // The button that had focus is gone; keep keyboard users in the page
      // instead of dropping them back at the very top.
      var main = document.getElementById("main");
      if (main) main.focus();
    });
  }

  // ---------------------------------------------------------------- forms
  // `data-confirm` asks before a form goes (delete a day, remove a user).
  // The question lives in an attribute rather than inline script, so a name
  // with an apostrophe can't break it and skip the question.
  //
  // `js-once` forms ignore a second press while the first is on its way,
  // since a double tap on Login used to send it twice and show an error.
  function initForms() {
    document.addEventListener("submit", function (e) {
      var form = e.target;
      var question = form.getAttribute("data-confirm");
      if (question && !window.confirm(question)) {
        e.preventDefault();
        return;
      }
      if (form.classList.contains("js-once")) {
        if (form.dataset.sent === "true") {
          e.preventDefault();
          return;
        }
        form.dataset.sent = "true";
        var btn = e.submitter || form.querySelector('[type="submit"]');
        if (btn) btn.setAttribute("aria-disabled", "true");
      }
    });

    // Coming back with the browser's Back button restores the page as it was
    // left -- mid-submit -- so make its buttons pressable again.
    window.addEventListener("pageshow", function (e) {
      if (!e.persisted) return;
      document.querySelectorAll("form.js-once").forEach(function (form) {
        delete form.dataset.sent;
        form.querySelectorAll('[aria-disabled="true"]').forEach(function (b) {
          b.removeAttribute("aria-disabled");
        });
      });
    });
  }

  // ----------------------------------------------------------- live timer
  // Counts on from what the server worked out when the page was made. The
  // old timer re-read the login clock time in the browser's own time zone,
  // which was hours out whenever the phone and the server disagreed.
  function initLiveTimer() {
    var card = document.getElementById("clock-card");
    if (!card) return;
    var state = card.dataset.dayState;
    if (state !== "working" && state !== "break") return;

    var worked = parseInt(card.dataset.workedSeconds || "0", 10);
    var onBreak = parseInt(card.dataset.breakSeconds || "0", 10);
    var timerEl = document.getElementById("live-timer-value");
    var breakEl = document.getElementById("break-timer");
    // Wall-clock time, not performance.now(): it keeps counting while a phone
    // sleeps, so the figure is right the moment the screen comes back on.
    var started = Date.now();

    function tick() {
      var elapsed = (Date.now() - started) / 1000;
      if (state === "working" && timerEl) timerEl.textContent = fmtHours((worked + elapsed) / 3600);
      if (state === "break" && breakEl) breakEl.textContent = fmtDuration((onBreak + elapsed) / 3600);
    }
    tick();
    setInterval(tick, 1000);
  }

  // ----------------------------------------------------------- status poll
  function breakNote(s) {
    return s.break_allowance_fmt ? ", allowing " + s.break_allowance_fmt + " more break" : "";
  }

  function setBanner(el, cls, html) {
    if (!el) return;
    el.className = "banner " + cls;
    el.innerHTML = html;
  }

  function renderWeekly(data) {
    var s = data.suggestion;
    var el = document.getElementById("weekly-banner");
    var left = data.weekly_remaining_fmt;
    if (data.weekly_complete) {
      setBanner(el, "banner-success", "You've hit your " + data.weekly_target_fmt + " weekly target" +
        (data.is_logged_in ? " — you can log out any time." : "."));
    } else if (s && !s.reached && s.lands_today) {
      setBanner(el, "banner-info", left + " left this week. At this pace, log out around <strong>" +
        s.suggested_time + "</strong> today to hit your target" + breakNote(s) + ".");
    } else if (s && !s.reached && !s.today_target_met) {
      setBanner(el, "banner-info", left + " left this week — more than today can cover. " +
        "Log out around <strong>" + s.today_time + "</strong> to finish today's " +
        s.today_target_fmt + breakNote(s) + ".");
    } else if (s && !s.reached) {
      setBanner(el, "banner-info", "Today's hours are done. " + left +
        " left this week — see the Saturday plan below.");
    } else {
      setBanner(el, "banner-muted", left + " left to reach your weekly target.");
    }

    var fill = document.getElementById("weekly-progress-fill");
    var bar = document.getElementById("weekly-progress");
    var pct = Math.min(100, data.weekly_progress_pct);
    if (fill) fill.style.width = pct + "%";
    if (bar) {
      bar.setAttribute("aria-valuenow", String(Math.round(pct)));
      bar.setAttribute("aria-valuetext", data.weekly_worked_fmt + " of " + data.weekly_target_fmt);
    }
    var fig = document.getElementById("weekly-worked-fig");
    if (fig) fig.textContent = data.weekly_worked_fmt;

    var finish = document.getElementById("finish-time");
    if (finish && s && s.today_time) finish.textContent = s.today_time;
  }

  // Rebuilds the Saturday plan from the same fields the page uses, so the
  // projected logout moves as the day is worked.
  function renderSaturday(sat) {
    var el = document.getElementById("saturday-banner");
    if (!el || !sat) return;
    if (sat.mode === "live") {
      if (sat.reached) {
        setBanner(el, "banner-success", "You've already covered your " + sat.target_fmt +
          " this week — log out on Saturday whenever suits you.");
      } else {
        setBanner(el, "banner-info", "Log out around <strong>" + sat.suggested_time +
          "</strong> today to complete your week.");
      }
    } else if (sat.mode === "done") {
      if (sat.week_complete) {
        setBanner(el, "banner-success", "Saturday's done and your weekly target is complete.");
      } else {
        setBanner(el, "banner-muted", "Saturday's logged — you worked " + sat.worked_fmt + ".");
      }
    } else if (sat.reached) {
      setBanner(el, "banner-success", "On current pace you'll hit " + sat.target_fmt +
        " without needing Saturday at all.");
    } else if (sat.projected_spills) {
      setBanner(el, "banner-muted", "About " + sat.remaining_fmt + " would be left for Saturday — " +
        "more than one day can hold. Adding a little to the days before will spread it out.");
    } else if (sat.projected_time) {
      setBanner(el, "banner-info", "If the rest of the week goes to plan, log out around <strong>" +
        sat.projected_time + "</strong> on Saturday (" + sat.remaining_fmt +
        " of work) to hit your " + sat.target_fmt + " target.");
    }
  }

  function initStatusPoll() {
    if (!window.STM_STATUS_URL) return;
    var card = document.getElementById("clock-card");
    var pageState = card ? card.dataset.dayState : null;

    function typingInCard() {
      var active = document.activeElement;
      return card && active && card.contains(active) && active.tagName === "INPUT";
    }

    function refresh() {
      fetch(window.STM_STATUS_URL, { headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data) return;
          // Logged in, out or on a break somewhere else -- another device or
          // tab. Show what's really happening instead of a screen that's
          // still ticking for a shift that has ended.
          if (pageState && data.day_state && data.day_state !== pageState) {
            if (!typingInCard()) window.location.reload();
            return;
          }
          renderWeekly(data);
          renderSaturday(data.saturday);
        })
        .catch(function () { /* offline for a moment - keep what's shown */ });
    }

    // Only poll while the page is actually being looked at: a tab left open
    // all day would otherwise keep using the host's CPU allowance for nobody.
    setInterval(function () { if (!document.hidden) refresh(); }, 30000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) refresh(); });
  }

  // ------------------------------------------------------------ break rows
  function initBreakRows() {
    var container = document.getElementById("break-rows");
    var addBtn = document.getElementById("add-break");
    var template = document.getElementById("break-row-template");
    if (!container || !addBtn || !template) return;
    var counter = 1000;

    addBtn.addEventListener("click", function () {
      var clone = template.content.cloneNode(true);
      counter += 1;
      // Each new row gets its own ids, so every label stays tied to its box.
      clone.querySelectorAll("[data-id]").forEach(function (input) {
        input.id = input.dataset.id + "-" + counter;
      });
      clone.querySelectorAll("[data-for]").forEach(function (label) {
        label.htmlFor = label.dataset.for + "-" + counter;
      });
      container.appendChild(clone);
      var first = container.lastElementChild.querySelector("input");
      if (first) first.focus();
    });

    container.addEventListener("click", function (e) {
      var btn = e.target.closest(".remove-break");
      if (!btn) return;
      var row = btn.closest(".break-row-input");
      if (container.querySelectorAll(".break-row-input").length <= 1) {
        row.querySelectorAll("input").forEach(function (i) { i.value = ""; });
        row.querySelector("input").focus();
        return;
      }
      // Move focus before the row -- and the button that had it -- goes.
      var neighbour = row.nextElementSibling || row.previousElementSibling;
      row.remove();
      var target = (neighbour && neighbour.querySelector("input")) || addBtn;
      target.focus();
    });
  }

  // --------------------------------------------------- profile picture pick
  function initAvatarPicker() {
    var input = document.getElementById("avatar-input");
    var label = document.getElementById("avatar-filename");
    if (!input || !label) return;
    var original = label.textContent;
    input.addEventListener("change", function () {
      label.textContent = input.files && input.files.length
        ? input.files[0].name + " — press Save profile to use it"
        : original;
    });
  }

  // ------------------------------------------------------------- day type
  // The hours box only matters for a custom day, and a reason only for a day
  // that isn't an ordinary working day.
  function initDayType() {
    var group = document.getElementById("day-type");
    if (!group) return;
    var custom = document.getElementById("custom-hours-field");
    var reason = document.getElementById("leave-reason-field");
    function sync() {
      var checked = group.querySelector("input:checked");
      var value = checked ? checked.value : "";
      if (custom) custom.hidden = value !== "custom";
      if (reason) reason.hidden = value === "";
    }
    group.addEventListener("change", sync);
    sync();
  }

  document.addEventListener("DOMContentLoaded", function () {
    initTheme();
    initFlashes();
    initForms();
    initLiveTimer();
    initStatusPoll();
    initBreakRows();
    initAvatarPicker();
    initDayType();
  });
})();
