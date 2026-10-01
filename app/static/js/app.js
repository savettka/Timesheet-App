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
      // The question can sit on the form, or on the one button that needs it.
      var question = form.getAttribute("data-confirm") ||
        (e.submitter && e.submitter.getAttribute("data-confirm"));
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
      // A Timesheet brought back by Back still shows lines as they were
      // before saving; saving that again would add them twice. Fetch it fresh.
      if (document.querySelector(".ts-form")) {
        window.location.reload();
        return;
      }
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

  // ------------------------------------------------------------ timesheet
  // The old way of copying: select a hidden box's text and copy that.
  function copyBySelecting(text) {
    var before = document.activeElement;
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "0";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    area.setSelectionRange(0, text.length);  // iPhones ignore select() alone
    var copied = false;
    try { copied = document.execCommand("copy"); } catch (e) { /* not allowed */ }
    document.body.removeChild(area);
    if (before && before.focus) before.focus();  // back where it was, not lost on the page
    return copied;
  }

  // The clipboard API, where the page may use it -- it needs a secure page
  // and can be refused -- and the old way where it can't.
  function copyText(text) {
    var api = navigator.clipboard && window.isSecureContext
      ? navigator.clipboard.writeText(text)
      : Promise.reject();
    return api.catch(function () {
      if (!copyBySelecting(text)) throw new Error("copy refused");
    });
  }

  // A line's time as its labels say it, from its boxes right now: "13:00 to 14:00".
  function lineSpan(line) {
    return (line.querySelector('[name="start"]').value || "?") + " to " +
      (line.querySelector('[name="end"]').value || "?");
  }

  // Each line's copy button puts it on the clipboard the way it was asked
  // for -- "Emails for a client (13:00 to 14:00 PM)" -- from what's in its
  // boxes right now, saved or not. AM or PM follows the end time.
  function initTimesheetCopy() {
    var list = document.querySelector(".ts-lines");
    if (!list) return;
    var status = document.getElementById("ts-copy-status");
    var note = document.getElementById("ts-copy-note");
    function say(message) { if (status) status.textContent = message; }
    list.querySelectorAll(".ts-copy").forEach(function (b) { b.hidden = false; });

    // Each line's text box and copy button are named for its time, and
    // the names follow when a time is changed.
    list.addEventListener("input", function (e) {
      if (e.target.type !== "time") return;
      var line = e.target.closest(".ts-line");
      var span = lineSpan(line);
      line.querySelector('[name="text"]').setAttribute("aria-label", "What you worked on, " + span);
      var button = line.querySelector(".ts-copy");
      if (!button.classList.contains("is-copied")) button.setAttribute("aria-label", "Copy " + span);
    });

    list.addEventListener("click", function (e) {
      var button = e.target.closest(".ts-copy");
      if (!button) return;
      var line = button.closest(".ts-line");
      var text = line.querySelector('[name="text"]').value.trim();
      var from = line.querySelector('[name="start"]').value;
      var to = line.querySelector('[name="end"]').value;
      button.classList.remove("is-copied", "is-failed");
      if (note) note.hidden = true;
      if (!text) {
        say("Nothing to copy yet: this line is empty.");
        line.querySelector('[name="text"]').focus();
        return;
      }
      var half = parseInt(to, 10) >= 12 ? " PM" : " AM";
      var copy = from && to ? text + " (" + from + " to " + to + half + ")" : text;
      button.focus();  // Safari doesn't focus a clicked button by itself
      copyText(copy).then(function () {
        button.classList.add("is-copied");
        button.setAttribute("aria-label", "Copied");
        say("Copied: " + copy);
        setTimeout(function () {
          button.classList.remove("is-copied");
          button.setAttribute("aria-label", "Copy " + lineSpan(line));
        }, 1600);
      }, function () {
        // Shown, not only spoken: otherwise the clipboard quietly keeps
        // whatever was in it before.
        button.classList.add("is-failed");
        var message = "Couldn't copy here. Select and copy this yourself: " + copy;
        say(message);
        if (note) { note.textContent = message; note.hidden = false; }
      });
    });
  }

  // A save refused for a line's times or words: start on that line. On a
  // phone the line in progress is the last one, below the fold by the
  // afternoon, so otherwise the page opens with it on screen -- unless
  // there's a message at the top (such as "Saved") that would scroll out
  // of sight.
  function initTimesheetScroll() {
    var invalid = document.querySelector('.ts-line [aria-invalid="true"]');
    if (invalid) {
      invalid.focus();
      invalid.scrollIntoView({ block: "center" });
      return;
    }
    var now = document.querySelector(".ts-line.is-now");
    if (!now || document.querySelector(".flash")) return;
    var tabBar = document.querySelector(".tab-bar");
    var covered = tabBar ? tabBar.offsetHeight : 0;
    if (now.getBoundingClientRect().bottom > window.innerHeight - covered) {
      now.scrollIntoView({ block: "center" });
    }
  }

  // ----------------------------------------------------- timesheet picture
  // "Download PNG": the day as a picture in STM's pastels -- the lavender
  // band with the mark, then every line with words in it -- drawn from the
  // boxes as they are, saved or not.
  var PNG = {
    width: 1080, pad: 56, scale: 2,
    band: "#eef0ff", brandText: "#3f3697", brandMuted: "#57519f", ink: "#151823", ink2: "#3d4354",
    ink3: "#555c6f", hair: "#e6e8ee", breakFill: "#fdefd2", breakText: "#7a4a00", font: "Inter, system-ui, sans-serif",
  };

  function clock12(hhmm) {
    var parts = hhmm.split(":"), h = parseInt(parts[0], 10);
    return (h % 12 || 12) + ":" + parts[1] + " " + (h < 12 ? "AM" : "PM");
  }

  // 12:47 – 2:00 PM, 11:30 AM – 12:30 PM: the line's time, as the app says it.
  function spanWords(from, to) {
    if (!from || !to) return (from ? clock12(from) : "?") + " – " + (to ? clock12(to) : "?");
    var a = clock12(from), b = clock12(to);
    return (a.slice(-2) === b.slice(-2) && to !== "00:00" ? a.slice(0, -3) : a) + " – " + b;
  }

  function wrapWords(ctx, text, width) {
    var lines = [], line = "";
    text.split(/\s+/).forEach(function (word) {
      while (ctx.measureText(word).width > width && word.length > 1) {  // one very long word
        var cut = word.length - 1;
        while (cut > 1 && ctx.measureText(word.slice(0, cut)).width > width) cut -= 1;
        if (line) { lines.push(line); line = ""; }
        lines.push(word.slice(0, cut));
        word = word.slice(cut);
      }
      var tryLine = line ? line + " " + word : word;
      if (line && ctx.measureText(tryLine).width > width) {
        lines.push(line);
        line = word;
      } else {
        line = tryLine;
      }
    });
    if (line) lines.push(line);
    return lines;
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function drawTimesheet(info, rows, logo) {
    var W = PNG.width, pad = PNG.pad, timeW = 250, gap = 28;
    var textW = W - 2 * pad - timeW - gap - 32;
    var canvas = document.createElement("canvas");
    var ctx = canvas.getContext("2d");
    ctx.font = "400 21px " + PNG.font;
    rows.forEach(function (row) { row.wrapped = wrapWords(ctx, row.text, textW); });

    var bandH = 140, top = bandH + (info.summary ? 76 : 40), headH = 46, lineH = 31;
    var bodyH = rows.reduce(function (sum, row) { return sum + Math.max(1, row.wrapped.length) * lineH + 30; }, 0);
    var H = top + headH + bodyH + 96;
    canvas.width = W * PNG.scale;
    canvas.height = H * PNG.scale;
    ctx.scale(PNG.scale, PNG.scale);
    ctx.textBaseline = "alphabetic";

    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = PNG.band;
    ctx.fillRect(0, 0, W, bandH);
    var textX = pad;
    if (logo) {
      ctx.drawImage(logo, pad - 6, 38, 64, 64);
      textX = pad + 70;
    }
    ctx.fillStyle = PNG.brandText;
    ctx.font = "700 34px " + PNG.font;
    ctx.fillText("STM", textX, 72);
    ctx.fillStyle = PNG.brandMuted;
    ctx.font = "400 18px " + PNG.font;
    ctx.fillText("Simple Time Manager", textX, 100);
    ctx.textAlign = "right";
    ctx.fillStyle = PNG.ink;
    ctx.font = "700 27px " + PNG.font;
    ctx.fillText(info.day, W - pad, 70);
    ctx.fillStyle = PNG.brandMuted;
    ctx.font = "400 18px " + PNG.font;
    ctx.fillText("Timesheet · " + info.who, W - pad, 100);
    ctx.textAlign = "left";

    if (info.summary) {
      ctx.fillStyle = PNG.ink2;
      ctx.font = "600 20px " + PNG.font;
      ctx.fillText(info.summary, pad, bandH + 46);
    }

    var y = top;
    ctx.fillStyle = PNG.band;
    roundRect(ctx, pad - 12, y, W - 2 * pad + 24, headH, 10);
    ctx.fill();
    ctx.fillStyle = PNG.brandText;
    ctx.font = "700 17px " + PNG.font;
    ctx.fillText("Time", pad + 4, y + 29);
    ctx.fillText("What I worked on", pad + timeW + gap, y + 29);
    y += headH;

    rows.forEach(function (row, i) {
      var h = Math.max(1, row.wrapped.length) * lineH + 30;
      if (row.isBreak) {
        ctx.fillStyle = PNG.breakFill;
        ctx.fillRect(pad - 12, y, W - 2 * pad + 24, h);
      }
      ctx.fillStyle = row.isBreak ? PNG.breakText : PNG.ink;
      ctx.font = "600 21px " + PNG.font;
      ctx.fillText(row.span, pad + 4, y + 15 + 23);
      ctx.fillStyle = row.isBreak ? PNG.breakText : PNG.ink2;
      ctx.font = "400 21px " + PNG.font;
      row.wrapped.forEach(function (text, n) {
        ctx.fillText(text, pad + timeW + gap, y + 15 + 23 + n * lineH);
      });
      y += h;
      if (i < rows.length - 1) {
        ctx.fillStyle = PNG.hair;
        ctx.fillRect(pad - 12, y - 0.5, W - 2 * pad + 24, 1);
      }
    });

    ctx.fillStyle = PNG.ink3;
    ctx.font = "400 16px " + PNG.font;
    ctx.fillText("Generated " + info.stamp, pad, H - 40);
    ctx.textAlign = "right";
    ctx.fillStyle = PNG.brandText;
    ctx.font = "700 16px " + PNG.font;
    ctx.fillText("STM · Simple Time Manager", W - pad, H - 40);
    return canvas;
  }

  function loadLogo() {
    var icon = document.querySelector('link[rel="icon"]');
    return new Promise(function (resolve) {
      if (!icon) return resolve(null);
      var img = new Image();
      img.onload = function () { resolve(img); };
      img.onerror = function () { resolve(null); };  // the words still make the picture
      img.src = icon.href;
    });
  }

  function initTimesheetPng() {
    var form = document.querySelector(".ts-form");
    var button = document.querySelector(".ts-png");
    var canvasOk = !!document.createElement("canvas").toBlob;
    if (!form || !button || !canvasOk) return;
    var status = document.getElementById("ts-copy-status");
    var note = document.getElementById("ts-copy-note");
    button.hidden = false;

    button.addEventListener("click", function () {
      var rows = [];
      form.querySelectorAll(".ts-line").forEach(function (line) {
        var text = line.querySelector('[name="text"]').value.trim();
        if (!text) return;
        rows.push({
          span: spanWords(line.querySelector('[name="start"]').value, line.querySelector('[name="end"]').value),
          text: text, isBreak: line.classList.contains("is-break"),
        });
      });
      if (note) note.hidden = true;
      if (!rows.length) {
        if (status) status.textContent = "Nothing to download yet: write in a line first.";
        if (note) { note.textContent = "Nothing to download yet: write in a line first."; note.hidden = false; }
        return;
      }
      button.setAttribute("aria-disabled", "true");
      var fonts = document.fonts && document.fonts.load ? document.fonts.load("600 21px Inter") : Promise.resolve();
      Promise.all([fonts.catch(function () {}), loadLogo()]).then(function (ready) {
        var d = form.dataset;
        var canvas = drawTimesheet({ day: d.pngDay, who: d.pngWho, summary: d.pngSummary, stamp: d.pngStamp },
                                   rows, ready[1]);
        canvas.toBlob(function (blob) {
          button.removeAttribute("aria-disabled");
          if (!blob) return;
          var url = URL.createObjectURL(blob);
          var link = document.createElement("a");
          link.href = url;
          link.download = d.pngName || "STM timesheet.png";
          document.body.appendChild(link);
          link.click();
          link.remove();
          setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
          if (status) status.textContent = "Downloaded " + link.download;
        }, "image/png");
      });
    });
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
    initTimesheetCopy();
    initTimesheetScroll();
    initTimesheetPng();
  });
})();
