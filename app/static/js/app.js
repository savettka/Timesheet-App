(function () {
  "use strict";

  // ------------------------------------------------------------- helpers
  function fmtHours(hours) {
    var total = Math.max(0, Math.round(hours * 60));
    return Math.floor(total / 60) + "h " + String(total % 60).padStart(2, "0") + "m";
  }

  // A phone (style.css's breakpoint): the Timesheet and Calendar are there
  // to look at, so nothing on them writes or changes a line.
  function onPhone() {
    return !!(window.matchMedia && window.matchMedia("(max-width: 899px)").matches);
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

  // ----------------------------------------------------------------- menu
  // The button at the top left hides the side menu entirely, and brings it
  // back. Kept like dark mode, and put on the page before it draws (base.html).
  function initMenuToggle() {
    var button = document.querySelector(".js-menu-toggle");
    if (!button) return;
    var root = document.documentElement;

    function sync() {
      var hidden = root.getAttribute("data-menu") === "hidden";
      var label = hidden ? "Show the menu" : "Hide the menu";
      button.setAttribute("aria-expanded", hidden ? "false" : "true");
      button.setAttribute("aria-label", label);
      button.title = label;
    }

    button.addEventListener("click", function () {
      var hide = root.getAttribute("data-menu") !== "hidden";
      if (hide) root.setAttribute("data-menu", "hidden");
      else root.removeAttribute("data-menu");
      try { localStorage.setItem("stm-menu", hide ? "hidden" : "shown"); } catch (e) { /* not kept - still applied */ }
      sync();
    });
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

  // -------------------------------------------------------------- profile
  // Your name or photo opens a small window of your own settings instead of
  // the whole Settings page, and Settings' Edit profile / Change password
  // open it at that part. Escape, the cross or a press outside shuts it.
  // Caught on the way down, before the loading circle sees the link.
  function initProfilePop() {
    var pop = document.getElementById("profile-pop");
    if (!pop || typeof pop.showModal !== "function") return;
    document.addEventListener("click", function (e) {
      var opener = e.target.closest && e.target.closest("[data-profile-open]");
      if (!opener || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
      e.preventDefault();
      var part = "pf-" + opener.getAttribute("data-profile-open");
      pop.querySelectorAll("details").forEach(function (d) { d.open = d.id === part; });
      if (!pop.open) pop.showModal();
      var first = document.getElementById(part);
      var field = first && first.querySelector("input:not([type=hidden]):not([hidden]):not(.file-input)");
      if (field) field.focus();
    }, true);
    pop.addEventListener("click", function (e) {
      if (e.target === pop || (e.target.closest && e.target.closest("[data-profile-close]"))) pop.close();
    });
    // Sending a form: out of the way, so the loading circle shows.
    pop.addEventListener("submit", function () { pop.close(); });
  }

  // More, in a phone's tab bar: the pages that don't fit there, in a sheet
  // from the bottom. Escape, the cross or a press outside shuts it.
  function initMoreSheet() {
    var sheet = document.getElementById("more-sheet");
    var button = document.querySelector("[data-more]");
    if (!sheet || !button || typeof sheet.showModal !== "function") return;
    button.addEventListener("click", function () {
      sheet.showModal();
      button.setAttribute("aria-expanded", "true");
    });
    sheet.addEventListener("click", function (e) {
      if (e.target === sheet || (e.target.closest && e.target.closest("[data-more-close]"))) sheet.close();
    });
    sheet.addEventListener("close", function () {
      button.setAttribute("aria-expanded", "false");
      button.focus();
    });
  }

  // ------------------------------------------------------------------ pay
  // The Pay page's Paid amounts read as text, like the rest of the row.
  // Pressing one turns it into a box (the currency fixed inside); a moment
  // after typing stops, on Enter, or when the box is left, it saves, and it
  // reads as text again with a tick. Esc puts back what it said before. The
  // server answers with each month's difference and the totals worked out
  // again, and the status says when it last saved. One save at a time;
  // anything typed meanwhile goes in the next, and leaving the page sends
  // what's waiting.
  function initPayAutosave() {
    var form = document.querySelector(".pay-form");
    if (!form || !window.fetch || !window.FormData) return;
    var status = document.getElementById("pay-status");
    var timer = null, busy = false, again = false, retry = null, touched = {};
    form.classList.add("is-live");

    function say(text, state) {
      if (!status) return;
      status.textContent = text;
      status.setAttribute("data-state", state);
    }
    function body() {
      var data = new FormData(form);
      data.set("autosave", "1");
      return data;
    }
    function pill(el, view) {
      if (!el) return;
      el.textContent = view.difference;
      el.className = "pay-diff " + view.tone;
    }
    function boxOf(paid) { return paid.querySelector("input"); }
    function keyOf(paid) { return boxOf(paid).name.slice(5); }

    function apply(res, sent) {
      Object.keys(res.rows || {}).forEach(function (key) {
        var row = form.querySelector('tr[data-month="' + key + '"]');
        if (!row) return;
        var view = res.rows[key], paid = row.querySelector(".paid");
        pill(row.querySelector(".pay-diff"), view);
        if (!paid) return;
        var input = boxOf(paid);
        input.setAttribute("aria-invalid", view.bad ? "true" : "false");
        paid.classList.toggle("is-bad", !!view.bad);
        if (view.bad) {
          paid.classList.add("is-editing");  // back to the box, to put right
          return;
        }
        paid.querySelector(".paid-amount").textContent = view.paid;
        paid.classList.toggle("is-empty", !view.paid);
        var label = input.getAttribute("aria-label");
        paid.querySelector(".paid-show").setAttribute("aria-label", label + ": " + (view.paid || "not added") + ". Change");
        if (sent[key] && document.activeElement !== input) paid.classList.add("is-saved");
      });
      if (res.totals) {
        pill(document.getElementById("pay-total-diff"), res.totals);
        var total = document.getElementById("pay-total-paid");
        if (total) total.textContent = res.totals.paid;
      }
    }
    function save() {
      clearTimeout(timer);
      clearTimeout(retry);
      timer = null;
      if (busy) { again = true; return; }
      busy = true;
      var sent = touched;
      touched = {};
      say("Saving\u2026", "saving");
      fetch(window.location.pathname, {
        method: "POST", body: body(), credentials: "same-origin",
        headers: { Accept: "application/json" }
      }).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      }).then(function (res) {
        apply(res, sent);
        if (res.ok) say("Saved \u00b7 " + res.saved_at, "saved");
        else say("Type amounts as numbers, like 23848", "error");
      }).catch(function () {
        Object.keys(sent).forEach(function (key) { touched[key] = true; });
        say("Couldn't save \u2014 trying again", "error");
        retry = setTimeout(save, 4000);
      }).then(function () {
        busy = false;
        if (again) { again = false; save(); }
      });
    }

    function edit(paid) {
      var input = boxOf(paid);
      paid.dataset.was = input.value;
      paid.classList.remove("is-saved");
      paid.classList.add("is-editing");
      input.focus();
      input.select();
    }
    function done(paid) {
      if (paid.classList.contains("is-bad")) return;  // stays a box until it's a number
      paid.classList.remove("is-editing");
    }

    form.addEventListener("click", function (e) {
      var show = e.target.closest(".paid-show");
      if (show) edit(show.closest(".paid"));
    });
    form.addEventListener("input", function (e) {
      if (!e.target.matches("input")) return;
      touched[keyOf(e.target.closest(".paid"))] = true;
      say("Editing\u2026", "editing");
      clearTimeout(timer);
      timer = setTimeout(save, AUTOSAVE_PAUSE_MS);
    });
    form.addEventListener("focusout", function (e) {
      if (!e.target.matches("input")) return;
      var paid = e.target.closest(".paid");
      if (timer) save();
      done(paid);
    });
    form.addEventListener("keydown", function (e) {
      if (!e.target.matches("input")) return;
      var paid = e.target.closest(".paid");
      if (e.key === "Enter") {
        e.preventDefault();
        e.target.blur();
        if (!paid.classList.contains("is-editing")) paid.querySelector(".paid-show").focus();
      } else if (e.key === "Escape" && paid.dataset.was !== undefined) {
        e.preventDefault();
        if (e.target.value !== paid.dataset.was) {
          e.target.value = paid.dataset.was;
          touched[keyOf(paid)] = true;
          timer = timer || -1;  // put back on the way out
        }
        paid.classList.remove("is-bad");
        e.target.blur();
        paid.querySelector(".paid-show").focus();
      }
    });
    form.addEventListener("submit", function (e) { e.preventDefault(); save(); });
    window.addEventListener("pagehide", function () {
      if (timer && navigator.sendBeacon) navigator.sendBeacon(window.location.pathname, body());
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

  function field(line, name) { return line.querySelector('[name="' + name + '"]'); }

  // A line's time as its labels say it, from its boxes right now: "13:00 to 14:00".
  function lineSpan(line) {
    return (field(line, "start").value || "?") + " to " + (field(line, "end").value || "?");
  }

  // Minutes after midnight from "13:00"; an end of 00:00 is the midnight
  // that ends the day. On a shift that runs past midnight (the form's
  // data-night), a time before the login, up to there, is after it: 00:30
  // on a 3 PM to 1 AM day is 1470, as the server reads it.
  function clockMinutes(value, name) {
    if (!/^\d\d:\d\d/.test(value || "")) return null;
    var m = parseInt(value.slice(0, 2), 10) * 60 + parseInt(value.slice(3, 5), 10);
    if (m === 0 && name === "end") return 1440;
    var form = document.querySelector(".ts-form[data-night]");
    if (form && m < parseInt(form.dataset.login, 10) && m <= parseInt(form.dataset.night, 10)) m += 1440;
    return m;
  }

  // How long a line is: 1h, 30m, 1h 13m (as the server says it).
  function lengthWords(minutes) {
    if (!(minutes > 0)) return "";
    var h = Math.floor(minutes / 60), m = minutes % 60;
    return [h ? h + "h" : "", m ? m + "m" : ""].filter(Boolean).join(" ");
  }

  function lineWords(line) { return field(line, "text").value.trim().split(/\s+/).join(" "); }

  // A line as Copy puts it on the clipboard: "Emails (13:00 to 14:00 PM)".
  function lineCopy(line) {
    var text = lineWords(line), from = field(line, "start").value, to = field(line, "end").value;
    var half = parseInt(to, 10) >= 12 ? " PM" : " AM";
    return from && to ? text + " (" + from + " to " + to + half + ")" : text;
  }

  // What a copy of a line says, as the server keeps it: "13:00-14:00 Emails".
  function copyMark(line) {
    return field(line, "start").value + "-" + field(line, "end").value + " " + lineWords(line);
  }

  // After a line's times or words change: its length, the names its boxes
  // and buttons are read out by, and whether it still says what was copied
  // -- change it after copying and it's no longer green.
  function refreshLine(line) {
    var span = lineSpan(line);
    var len = line.querySelector(".ts-len");
    if (len) {
      var a = clockMinutes(field(line, "start").value, "start"), b = clockMinutes(field(line, "end").value, "end");
      len.textContent = a === null || b === null ? "" : lengthWords(b - a);
    }
    field(line, "text").setAttribute("aria-label", "What you worked on, " + span);
    var copied = !!line.dataset.copied && line.dataset.copied === copyMark(line);
    line.classList.toggle("is-copied", copied);
    var copy = line.querySelector(".ts-copy");
    if (copy) copy.setAttribute("aria-label", (copied ? "Copied " : "Copy ") + span);
    var reset = line.querySelector(".ts-reset");
    if (reset) reset.setAttribute("aria-label", "Reset " + span + " to not copied");
    var del = line.querySelector(".ts-delete");
    del.setAttribute("aria-label", "Delete " + span);
    if (del.hasAttribute("data-confirm")) del.setAttribute("data-confirm", "Delete the line from " + span + "?");
  }

  // "3 of 7 copied", from the lines with words in them (breaks aside).
  function refreshCount() {
    var chip = document.getElementById("ts-copied-count");
    if (!chip) return;
    var lines = [].filter.call(document.querySelectorAll(".ts-line"), function (line) {
      return !line.classList.contains("is-break") && !line.classList.contains("is-squeezed") && lineWords(line);
    });
    var done = lines.filter(function (line) { return line.classList.contains("is-copied"); }).length;
    chip.querySelector("b").textContent = done + " of " + lines.length;
    chip.hidden = !lines.length;
    chip.parentNode.hidden = !chip.parentNode.querySelector("li:not([hidden])");
  }

  // Each line's copy button puts it on the clipboard the way it was asked
  // for -- "Emails for a client (13:00 to 14:00 PM)" -- from what's in its
  // boxes right now, saved or not. AM or PM follows the end time. The line
  // then stays green, on any device, until it's reset or changed: a saved
  // line is marked there and then; one with changes is saved with the page.
  function initTimesheetCopy() {
    var form = document.querySelector(".ts-form");
    var list = form && form.querySelector(".ts-lines");
    if (!list) return;
    var status = document.getElementById("ts-copy-status");
    var note = document.getElementById("ts-copy-note");
    function say(message) { if (status) status.textContent = message; }
    list.querySelectorAll(".ts-copy").forEach(function (b) { b.hidden = false; });

    list.addEventListener("input", function (e) {
      var line = e.target.closest(".ts-line");
      if (!line) return;
      refreshLine(line);
      refreshCount();
    });

    // Sent with the page, as if its button had been pressed.
    function submitWith(name, value) {
      var input = document.createElement("input");
      input.type = "hidden";
      input.name = name;
      input.value = value;
      form.appendChild(input);
      if (form.requestSubmit) form.requestSubmit(); else form.submit();
      if (form.dataset.sent !== "true") input.remove();  // the save was called off
    }

    function mark(line, on) {
      var name = on ? "copied" : "uncopied";
      var index = String([].indexOf.call(list.children, line));
      var id = field(line, "id").value;
      var changed = !id || field(line, "start").value !== field(line, "was_start").value ||
        field(line, "end").value !== field(line, "was_end").value || lineWords(line) !== field(line, "was_text").value;
      if (on) line.dataset.copied = copyMark(line); else delete line.dataset.copied;
      refreshLine(line);
      refreshCount();
      if (changed || !window.fetch || !window.FormData) return submitWith(name, index);
      var body = new FormData();
      body.append("id", id);
      body.append("mark", on ? "1" : "0");
      body.append("start", field(line, "start").value);
      body.append("end", field(line, "end").value);
      body.append("text", lineWords(line));
      fetch(form.dataset.markUrl, { method: "POST", body: body, credentials: "same-origin" })
        .then(function (r) {
          if (!r.ok || (r.headers.get("Content-Type") || "").indexOf("json") < 0) throw new Error("not marked");
          return r.json();
        })
        .then(function (done) {
          // Changed on another device since this page opened: show it as it is.
          if (on && !done.copied) window.location.reload();
        })
        .catch(function () { submitWith(name, index); });
    }

    list.addEventListener("click", function (e) {
      var reset = e.target.closest(".ts-reset");
      if (reset) {
        e.preventDefault();
        mark(reset.closest(".ts-line"), false);
        say("Reset to not copied.");
        return;
      }
      var button = e.target.closest(".ts-copy");
      if (!button) return;
      var line = button.closest(".ts-line");
      button.classList.remove("is-failed");
      if (note) note.hidden = true;
      if (!lineWords(line)) {
        say("Nothing to copy yet: this line is empty.");
        field(line, "text").focus();
        return;
      }
      var copy = lineCopy(line);
      button.focus();  // Safari doesn't focus a clicked button by itself
      copyText(copy).then(function () {
        say("Copied: " + copy);
        mark(line, true);
      }, function () {
        // Shown, not only spoken: otherwise the clipboard quietly keeps
        // whatever was in it before.
        button.classList.add("is-failed");
        var message = "Couldn't copy here. Select and copy this yourself: " + copy;
        say(message);
        if (note) { note.textContent = message; note.hidden = false; }
      });
    });

    // The line menu's Mark posted and Mark not posted: what Copy's mark and
    // the reset button do, without the clipboard.
    list.addEventListener("stm:mark", function (e) {
      var line = e.target.closest(".ts-line");
      if (!line || !lineWords(line)) return;
      mark(line, e.detail.on);
      say(e.detail.on ? "Marked as posted." : "No longer marked as posted.");
    });
  }

  // Times follow: change a line's end and the next line starts there;
  // change a start and the line before ends there -- so the day never has a
  // gap or an overlap, and the times posted elsewhere add up. A line pushed
  // past entirely is squeezed to nothing and goes on Save (asking first if
  // it has words). Each change is worked out afresh from the times as they
  // were before that box was touched, so typing 9 on the way to 9:30
  // leaves no trace of 9:00 behind.
  function initTimesheetFollow() {
    var form = document.querySelector(".ts-form");
    var list = form && form.querySelector(".ts-lines");
    if (!list) return;
    var lines = [].slice.call(list.children);
    var editing = null, base = [];

    // An empty line no one has saved is only a suggestion: moving it with a
    // neighbour moves its "was" times too, so the server doesn't ask for words.
    function loose(line) {
      return !field(line, "id").value && !lineWords(line) && !field(line, "was_text").value;
    }
    function remember() {
      lines.forEach(function (line) {
        field(line, "start").dataset.last = field(line, "start").value;
        field(line, "end").dataset.last = field(line, "end").value;
      });
    }
    function snapshot() {
      base = lines.map(function (line) {
        return {
          start: field(line, "start").dataset.last, end: field(line, "end").dataset.last,
          wasStart: field(line, "was_start").value, wasEnd: field(line, "was_end").value,
          squeezed: line.classList.contains("is-squeezed"),
        };
      });
    }
    function put(line, name, value) {
      field(line, name).value = value;
      if (loose(line)) field(line, "was_" + name).value = value;
    }

    function follow(i, name, value) {
      // Back to how things were, all but the box being typed in.
      lines.forEach(function (line, j) {
        var b = base[j];
        if (j !== i || name !== "start") field(line, "start").value = b.start;
        if (j !== i || name !== "end") field(line, "end").value = b.end;
        // Only a suggestion's "was" times are the follow's to put back; a
        // saved line's belong to the autosave, which may have moved them on.
        if (loose(line)) {
          field(line, "was_start").value = b.wasStart;
          field(line, "was_end").value = b.wasEnd;
        }
        line.classList.toggle("is-squeezed", b.squeezed);
      });
      var own = lines[i];
      var a = clockMinutes(field(own, "start").value, "start"), z = clockMinutes(field(own, "end").value, "end");
      if (a !== null && z !== null && z > a) own.classList.remove("is-squeezed");
      // Half-typed (an end before its own start): nothing beside it moves yet.
      if (a === null || z === null || z <= a) {
        lines.forEach(refreshLine);
        refreshCount();
        return;
      }

      var dir = name === "end" ? 1 : -1;
      var near = dir > 0 ? "start" : "end", far = dir > 0 ? "end" : "start";
      var to = clockMinutes(value, name), was = clockMinutes(base[i][name], name);
      for (var j = i + dir; to !== null && was !== null && j >= 0 && j < lines.length; j += dir) {
        var line = lines[j];
        var nearAt = clockMinutes(base[j][near], near), farAt = clockMinutes(base[j][far], far);
        if (nearAt === null || farAt === null) break;
        // Only a line that met this one, or that it now runs into.
        if (nearAt !== was && !(dir > 0 ? nearAt < to : nearAt > to)) break;
        put(line, near, value);
        if (dir > 0 ? farAt > to : farAt < to) {
          line.classList.remove("is-squeezed");
          break;
        }
        put(line, far, value);
        line.classList.add("is-squeezed");
        was = farAt;
      }
      lines.forEach(refreshLine);
      refreshCount();
    }

    remember();
    list.addEventListener("input", function (e) {
      var box = e.target;
      if (box.type !== "time") return;
      if (box !== editing) {
        snapshot();
        editing = box;
      }
      follow(lines.indexOf(box.closest(".ts-line")), box.name, box.value);
      remember();
    });

    // On Save, squeezed lines go; asked first if any has words in it.
    form.addEventListener("submit", function (e) {
      var squeezed = lines.filter(function (line) { return line.classList.contains("is-squeezed"); });
      var worded = squeezed.map(lineWords).filter(Boolean);
      if (worded.length && !window.confirm(
        (worded.length === 1 ? "Remove the line \u201c" + worded[0] + "\u201d? The line beside it now has its time."
                             : "Remove " + worded.length + " lines (\u201c" + worded.join("\u201d, \u201c") +
                               "\u201d)? The lines beside them now have their time."))) {
        e.preventDefault();
        e.stopPropagation();  // not counted as sent: Save can be pressed again
        return;
      }
      lines.forEach(function (line) { field(line, "drop").value = line.classList.contains("is-squeezed") ? "1" : ""; });
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
    ink3: "#555c6f", hair: "#e6e8ee", breakFill: "#fdefd2", breakText: "#7a4a00", font: "Figtree, system-ui, sans-serif",
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
        if (!text || line.classList.contains("is-squeezed")) return;
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
      var fonts = document.fonts && document.fonts.load ? document.fonts.load("600 21px Figtree") : Promise.resolve();
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

  // ------------------------------------------------------------ slides
  // Each part slides in after the one before it: its place among its
  // siblings, as --i (capped, so a long list doesn't keep you waiting).
  function initSlideOrder() {
    var groups = document.querySelectorAll(
      ".dash-grid, .rp-stats, .ts-lines, .kb, .kb-list, .wb, .ta-card, .tw tbody, .cg-body, .ds-days, .fill-days, " +
      ".timer-again-row, .rp-tasks, .rp-bars, .days tbody, .pay-table tbody, .content");
    groups.forEach(function (group) {
      var n = 0;
      [].forEach.call(group.children, function (child) {
        child.style.setProperty("--i", Math.min(n, 12));
        n += 1;
      });
    });
  }

  // ------------------------------------------------------------ autosave
  // The Timesheet saves itself: a moment after typing stops, or when a box
  // is left, the page's lines go to the server, which answers with them as
  // saved -- new lines' ids, what each now holds -- and the status says when.
  // One save at a time; changes made meanwhile go in the next. Leaving the
  // page sends anything still waiting. Enter saves there and then.
  var AUTOSAVE_PAUSE_MS = 900;

  function initAutosave() {
    var form = document.querySelector(".ts-form");
    var status = document.getElementById("ts-autosave");
    if (!form || !status || !window.fetch || !window.FormData) return;
    var text = status.querySelector(".ts-autosave-text");
    var save = form.querySelector(".ts-save");
    if (save) save.hidden = true;
    status.hidden = false;
    var timer = null, busy = false, waiting = false, failures = 0;

    function say(words, state) {
      text.textContent = words;
      status.dataset.state = state;
    }
    function field(line, name) { return line.querySelector('[name="' + name + '"]'); }
    function body() {
      // Squeezed lines without words go; one with words waits (and says so).
      form.querySelectorAll(".ts-line").forEach(function (line) {
        var squeezed = line.classList.contains("is-squeezed") && !field(line, "text").value.trim();
        field(line, "drop").value = squeezed || line.dataset.removed ? "1" : "";
      });
      var data = new FormData(form);
      data.append("autosave", "1");
      return data;
    }
    function showError(line, message, on) {
      var p = line.querySelector(".ts-error");
      line.querySelectorAll('[aria-invalid="true"]').forEach(function (el) { el.removeAttribute("aria-invalid"); });
      line.classList.toggle("has-error", !!message);
      if (!message) { if (p) p.remove(); return; }
      if (!p) {
        p = document.createElement("p");
        p.className = "ts-error";
        line.appendChild(p);
      }
      p.textContent = message;
      var boxes = on === "text" ? [field(line, "text")] : [field(line, "start"), field(line, "end")];
      boxes.forEach(function (el) { el.setAttribute("aria-invalid", "true"); });
    }
    function apply(result) {
      var lines = form.querySelectorAll(".ts-line");
      var problems = 0;
      result.lines.forEach(function (r, i) {
        var line = lines[i];
        if (!line) return;
        if (!r) {  // as saved already: nothing wrong with it any more
          showError(line, "");
          return;
        }
        if (r.removed) {
          line.dataset.removed = "1";
          line.hidden = true;
          return;
        }
        if (r.error) {
          problems += 1;
          showError(line, r.error, r.error_on);
          return;
        }
        showError(line, "");
        field(line, "id").value = r.id;
        field(line, "was_start").value = r.was_start;
        field(line, "was_end").value = r.was_end;
        field(line, "was_text").value = r.was_text;
      });
      if (problems) say("Saved " + result.saved_at + " · " + problems + (problems > 1 ? " lines need" : " line needs") + " a look", "warn");
      else say("Saved · " + result.saved_at, "saved");
    }
    function run() {
      clearTimeout(timer);
      timer = null;
      if (busy) { waiting = true; return; }
      busy = true;
      waiting = false;
      say("Saving…", "saving");
      fetch(form.action, { method: "POST", body: body(), credentials: "same-origin",
                           headers: { "Accept": "application/json" } })
        .then(function (r) {
          if (!r.ok || (r.headers.get("Content-Type") || "").indexOf("json") < 0) throw new Error("not saved");
          return r.json();
        })
        .then(function (result) { failures = 0; apply(result); })
        .catch(function () {
          failures += 1;
          say("Not saved yet · trying again", "error");
          waiting = true;
          timer = setTimeout(run, Math.min(30000, 3000 * failures));
        })
        .then(function () {
          busy = false;
          if (waiting && !timer) run();
          else status.dispatchEvent(new CustomEvent("stm:saved"));
        });
    }
    function soon(ms) {
      clearTimeout(timer);
      say("Editing…", "editing");
      timer = setTimeout(run, ms);
    }
    form.addEventListener("input", function (e) {
      if (e.target.matches('input[name="text"], input[type="time"]')) soon(AUTOSAVE_PAUSE_MS);
    });
    form.addEventListener("focusout", function (e) {
      if (timer && e.target.matches('input[name="text"], input[type="time"]')) soon(150);
    });
    // Enter, or a Save left on the page: save now, without leaving.
    form.addEventListener("submit", function (e) {
      if (e.defaultPrevented) return;
      var by = e.submitter;
      if (!by || !(by === save || (by.type === "submit" && !by.name && by.classList.contains("visually-hidden")))) return;
      e.preventDefault();
      e.stopPropagation();
      run();
    });
    // The batch bar, before it acts on lines: anything typed is saved first,
    // and then it goes on (stm:flush, whose detail.done is called).
    var flushed = [];
    form.addEventListener("stm:flush", function (e) {
      e.detail.handled = true;
      flushed.push(e.detail.done);
      if (timer) run();
      else if (!busy && !waiting) settle();
    });
    function settle() {
      if (busy || waiting || timer) return;
      var calls = flushed;
      flushed = [];
      calls.forEach(function (done) { done(); });
    }
    status.addEventListener("stm:saved", settle);
    // Leaving with a save still waiting: send it on the way out.
    window.addEventListener("pagehide", function () {
      if (timer || waiting) {
        clearTimeout(timer);
        if (navigator.sendBeacon) navigator.sendBeacon(form.action, body());
      }
    });
    window.addEventListener("online", function () { if (waiting && !busy) run(); });
  }

  // ------------------------------------------------------------ boards
  // Drag a card from Written to Posted (or back) and its line's copied mark
  // follows, saved there and then; the card's button does the same without
  // a drag, which is the way on a phone.
  function initBoards() {
    var board = document.querySelector(".kb[data-mark-url]");
    if (!board) return;
    var status = document.getElementById("kb-status");
    var dragging = null;

    function tally(col) {
      var cards = col.querySelectorAll(".kb-card");
      var minutes = [].reduce.call(cards, function (sum, c) { return sum + parseInt(c.dataset.minutes, 10); }, 0);
      col.querySelector(".kb-count").textContent = cards.length;
      var h = Math.floor(minutes / 60), m = minutes % 60;
      col.querySelector(".kb-hours").textContent = (h ? h + "h" : "") + (h && m ? " " : "") + (m || !h ? m + "m" : "");
      var none = col.querySelector(".kb-none");
      if (none) none.hidden = cards.length > 0;
    }
    function restyle(card, posted) {
      card.classList.toggle("kind-posted", posted);
      card.classList.toggle("kind-written", !posted);
      var form = card.querySelector(".kb-move");
      if (!form) return;
      var text = card.querySelector(".kb-text").textContent;
      form.querySelector('[name="mark"]').value = posted ? "0" : "1";
      form.querySelector(".kb-btn").setAttribute("aria-label",
        posted ? "Move " + text + " back to written" : "Mark " + text + " as posted");
    }
    function move(card, to) {
      var from = card.closest(".kb-col");
      if (from === to) return;
      var posted = to.dataset.status === "posted";
      // Moving the card would drop the keyboard's place; keep it on the card.
      var focused = card.contains(document.activeElement) ? document.activeElement : null;
      to.querySelector(".kb-list").appendChild(card);
      if (focused) focused.focus();
      card.classList.remove("is-dropped");
      void card.offsetWidth;  // restart the settle, if it's moved twice
      card.classList.add("is-dropped");
      restyle(card, posted);
      tally(from); tally(to);
      var body = new FormData();
      body.append("id", card.dataset.id);
      body.append("mark", posted ? "1" : "0");
      fetch(board.dataset.markUrl, { method: "POST", body: body, credentials: "same-origin",
                                     headers: { "Accept": "application/json" } })
        .then(function (r) { if (!r.ok) throw new Error("not saved"); return r.json(); })
        .then(function () {
          if (status) status.textContent = (posted ? "Posted: " : "Back to written: ") + card.querySelector(".kb-text").textContent;
        })
        .catch(function () {
          from.querySelector(".kb-list").appendChild(card);  // put it back: nothing was saved
          restyle(card, !posted);
          tally(from); tally(to);
          if (status) status.textContent = "Couldn't save that. Try again.";
        });
    }

    board.addEventListener("dragstart", function (e) {
      var card = e.target.closest('.kb-card[draggable="true"]');
      if (!card) return;
      dragging = card;
      card.classList.add("is-dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", card.dataset.id);
    });
    board.addEventListener("dragend", function () {
      if (dragging) dragging.classList.remove("is-dragging");
      dragging = null;
      board.querySelectorAll(".kb-col.is-over").forEach(function (c) { c.classList.remove("is-over"); });
    });
    board.addEventListener("dragover", function (e) {
      var col = e.target.closest('.kb-col[data-status="written"], .kb-col[data-status="posted"]');
      if (!col || !dragging) return;
      e.preventDefault();
      board.querySelectorAll(".kb-col.is-over").forEach(function (c) { if (c !== col) c.classList.remove("is-over"); });
      col.classList.add("is-over");
    });
    board.addEventListener("drop", function (e) {
      var col = e.target.closest('.kb-col[data-status="written"], .kb-col[data-status="posted"]');
      if (!col || !dragging) return;
      e.preventDefault();
      col.classList.remove("is-over");
      move(dragging, col);
    });
    // The line menu's Mark posted and Mark not posted: the same move.
    board.addEventListener("stm:mark", function (e) {
      var card = e.target.closest(".kb-card[data-id]");
      var to = card && board.querySelector('.kb-col[data-status="' + (e.detail.on ? "posted" : "written") + '"]');
      if (to) move(card, to);
    });
    // The button: the same move, without leaving the page.
    board.addEventListener("submit", function (e) {
      var form = e.target.closest(".kb-move");
      if (!form || !window.fetch) return;
      e.preventDefault();
      e.stopPropagation();
      var card = form.closest(".kb-card");
      var to = board.querySelector('.kb-col[data-status="' + (form.querySelector('[name="mark"]').value === "1" ? "posted" : "written") + '"]');
      move(card, to);
    }, true);
  }

  // Reports: the day under the pointer (or tapped) in words, for a month
  // whose bars are too narrow for their numbers.
  function initReportBars() {
    var readout = document.getElementById("rp-readout");
    if (!readout) return;
    function show(e) {
      var bar = e.target.closest && e.target.closest(".rp-bar");
      if (bar) readout.textContent = bar.getAttribute("aria-label");
    }
    var bars = document.querySelector(".rp-bars");
    bars.addEventListener("mouseover", show);
    bars.addEventListener("focusin", show);
    bars.addEventListener("click", show);
  }

  // The Timesheet's date box goes as soon as a date is picked.
  function initDayJump() {
    var form = document.querySelector(".day-jump");
    if (!form) return;
    var input = form.querySelector('input[type="date"]');
    var go = form.querySelector(".day-jump-go");
    if (go) go.hidden = true;
    input.addEventListener("change", function () {
      if (input.value && input.value !== input.defaultValue) {
        if (form.requestSubmit) form.requestSubmit(); else form.submit();
      }
    });
  }

  // ------------------------------------------------------------ copy week
  // The week's lines, day by day, onto the clipboard: to paste into the
  // other timesheet in one go.
  function initWeekCopy() {
    var button = document.querySelector(".tw-copy");
    if (!button) return;
    var status = document.getElementById("tw-status");
    var word = button.querySelector("span");
    var said = word.textContent;  // "Copy week", or the calendar link's "Copy"
    button.hidden = false;
    button.addEventListener("click", function () {
      copyText(button.dataset.copy).then(function () {
        button.classList.add("is-copied");
        word.textContent = "Copied";
        if (status) status.textContent = "Copied.";
        setTimeout(function () { button.classList.remove("is-copied"); word.textContent = said; }, 1800);
      }, function () {
        if (status) status.textContent = "Couldn't copy here.";
        word.textContent = "Couldn't copy";
      });
    });
  }

  // ------------------------------------------------------------ batch
  // Tick boxes on Timesheet lines -- Day, Week and All -- and a bar for the
  // ticked ones: copy them, mark them posted or not, or delete them, all at
  // once. Shift+click ticks a run of them; Esc clears.
  function initBatch() {
    var bar = document.getElementById("bb");
    if (!bar) return;
    var status = document.getElementById("bb-status");
    var count = bar.querySelector(".bb-count b");
    var day = document.querySelector(".ts-form");  // the Day page: saved as typed
    var last = null, sending = false;
    document.querySelectorAll(".pick").forEach(function (p) { p.hidden = false; });

    function picks() { return [].slice.call(document.querySelectorAll(".js-pick")); }
    function chosen() { return picks().filter(function (p) { return p.checked && !p.disabled; }); }
    function say(words) { if (status) status.textContent = words; }
    function refresh() {
      picks().forEach(function (p) {
        var row = p.closest(".ts-line, .ta-line, tr");
        if (row) row.classList.toggle("is-picked", p.checked && !p.disabled);
      });
      var n = chosen().length;
      count.textContent = n;
      if (bar.hidden !== !n) {
        bar.hidden = !n;
        if (n) document.documentElement.setAttribute("data-batch", "");
        else document.documentElement.removeAttribute("data-batch");
      }
    }
    function clear() {
      var first = chosen()[0];
      picks().forEach(function (p) { p.checked = false; });
      refresh();
      if (first) first.focus();
    }
    // How many lines are ticked: a Week row is every line it adds up.
    function lineCount() {
      return chosen().reduce(function (n, p) { return n + (p.dataset.ids ? p.dataset.ids.split(",").length : 1); }, 0);
    }
    function ids() {
      return chosen().map(function (p) {
        var line = p.closest(".ts-line");
        return line ? field(line, "id").value : p.dataset.ids || "";
      }).filter(Boolean).join(",");
    }
    // [day, heading, start, words] for each ticked line; a Day line from its boxes.
    function linesOf(p) {
      var line = p.closest(".ts-line");
      if (line) return [["", "", clockMinutes(field(line, "start").value, "start") || 0, lineCopy(line)]];
      try { return JSON.parse(p.dataset.lines || "[]"); } catch (err) { return []; }
    }
    // As Copy week puts it: a heading a day, when there's more than one day.
    function copyWords() {
      var list = [].concat.apply([], chosen().map(linesOf));
      list.sort(function (a, b) { return a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[2] - b[2]; });
      var many = list.some(function (l) { return l[0] !== list[0][0]; });
      var out = [], at = null;
      list.forEach(function (l) {
        if (many && l[0] !== at) {
          if (out.length) out.push("");
          out.push(l[1]);
          at = l[0];
        }
        out.push(l[3]);
      });
      return out.join("\n");
    }
    function send(action) {
      if (sending) return;
      var go = function () {
        var list = ids();
        if (!list) { say("Those lines aren't saved yet."); return; }
        sending = true;
        bar.querySelector('[name="ids"]').value = list;
        bar.querySelector('[name="action"]').value = action;
        bar.submit();
      };
      if (!day) return go();
      // Anything typed is saved first, so the lines go as they read.
      var detail = { done: go, handled: false };
      day.dispatchEvent(new CustomEvent("stm:flush", { detail: detail }));
      if (!detail.handled) go();
    }

    if (day) {
      // A Day line can be ticked once it has words.
      day.addEventListener("input", function (e) {
        if (!e.target.matches('input[name="text"]')) return;
        var p = e.target.closest(".ts-line").querySelector(".js-pick");
        if (!p) return;
        p.disabled = !e.target.value.trim();
        if (p.disabled) p.checked = false;
        refresh();
      });
    }
    document.addEventListener("change", function (e) {
      if (e.target.matches && e.target.matches(".js-pick")) refresh();
    });
    document.addEventListener("click", function (e) {
      var p = e.target.closest && e.target.closest(".js-pick");
      if (!p) return;
      if (e.shiftKey && last && last !== p) {
        var all = picks(), i = all.indexOf(last), j = all.indexOf(p);
        if (i >= 0 && j >= 0) {
          all.slice(Math.min(i, j), Math.max(i, j) + 1).forEach(function (q) { if (!q.disabled) q.checked = p.checked; });
          refresh();
        }
      }
      last = p;
    });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || bar.hidden || !e.target.closest) return;
      if (!e.target.closest(".js-pick") && !bar.contains(e.target)) return;
      e.preventDefault();
      clear();
    });
    bar.querySelector(".bb-all").addEventListener("click", function () {
      picks().forEach(function (p) { if (!p.disabled) p.checked = true; });
      refresh();
    });
    bar.querySelector(".bb-clear").addEventListener("click", clear);
    bar.addEventListener("click", function (e) {
      var button = e.target.closest(".bb-act");
      if (!button) return;
      var action = button.dataset.action, n = lineCount();
      if (action === "delete" && !window.confirm("Delete " + n + (n === 1 ? " line?" : " lines?"))) return;
      if (action !== "copy") return send(action);
      // Copied, then marked posted -- as one line's Copy does.
      var word = button.querySelector(".bb-word");
      copyText(copyWords()).then(function () {
        say("Copied " + n + (n === 1 ? " line." : " lines."));
        send("copy");
      }, function () {
        say("Couldn't copy here.");
        word.textContent = "Couldn't copy";
        setTimeout(function () { word.textContent = "Copy"; }, 2400);
      });
    });
    window.addEventListener("pageshow", function (e) { if (e.persisted) sending = false; });
    refresh();
  }

  // ------------------------------------------------------------ reminders
  // Every page asks /api/reminders now and then. The server sends the
  // weekly report once it's due, and says how long until it will be: the
  // page asks again then, even from a tab in the background -- there's no
  // scheduler on the server to do it.
  //
  // The reminders that apply come back too: a bar at the top of the page,
  // each one put away (for its day) with its x; and a browser notification
  // for each new one, where Settings has turned them on.
  var SVG = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">';
  function stored(name) {
    try { return JSON.parse(localStorage.getItem(name) || "{}") || {}; } catch (e) { return {}; }
  }
  function store(name, value) {
    try { localStorage.setItem(name, JSON.stringify(value)); } catch (e) { /* storage blocked */ }
  }
  function notifying() {
    try {
      return "Notification" in window && Notification.permission === "granted" && localStorage.getItem("stm-notify") === "1";
    } catch (e) { return false; }
  }

  function initReminders() {
    var url = document.body.dataset.remindersUrl;
    if (!url || !window.fetch) return;
    var bar = document.getElementById("rm-bar");
    var due = null, asked = 0, MIN_GAP = 60000;

    function show(list) {
      if (!bar) return;
      var off = stored("stm-rm-off"), keep = {};
      bar.innerHTML = "";
      list.forEach(function (r) {
        if (off[r.key]) { keep[r.key] = true; return; }
        var li = document.createElement("li");
        li.className = "rm-item";
        li.innerHTML = SVG + '<path d="M12 4 2.5 20h19z"/><path d="M12 10v4M12 17h.01"/></svg><a></a>' +
          '<button type="button" aria-label="Put this reminder away" title="Put away">' + SVG +
          '<path d="M6 6l12 12M18 6 6 18"/></svg></button>';
        var link = li.querySelector("a");
        link.href = r.href;
        link.textContent = r.text;
        li.querySelector("button").addEventListener("click", function () {
          var now = stored("stm-rm-off");
          now[r.key] = true;
          store("stm-rm-off", now);
          var next = li.nextElementSibling || li.previousElementSibling;
          li.remove();
          bar.hidden = !bar.children.length;
          if (next) next.querySelector("button").focus();
        });
        bar.appendChild(li);
      });
      store("stm-rm-off", keep);  // only today's: the rest can go
      bar.hidden = !bar.children.length;
    }
    function tell(list) {
      if (!notifying()) return;
      var told = stored("stm-rm-told"), keep = {};
      list.forEach(function (r) {
        keep[r.key] = true;
        if (told[r.key]) return;
        try {
          var note = new Notification("STM", { body: r.text, tag: r.key });
          note.onclick = function () { window.focus(); window.location.href = r.href; };
        } catch (e) { /* not allowed from this page */ }
      });
      store("stm-rm-told", keep);
    }
    function ask() {
      asked = Date.now();
      fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data) return;
          clearTimeout(due);
          if (data.report_in !== null && data.report_in !== undefined) {
            due = setTimeout(ask, (Math.max(0, data.report_in) + 5) * 1000);
          }
          show(data.reminders || []);
          tell(data.reminders || []);
        })
        .catch(function () { /* offline for a moment: the next page asks */ });
    }
    ask();  // every page: the one after a logout has to start the wait
    // Every two minutes while looked at; while hidden, only to notify.
    setInterval(function () {
      var gap = document.hidden ? 5 * 60000 : 2 * 60000;
      if ((!document.hidden || notifying()) && Date.now() - asked >= gap - 1000) ask();
    }, 60000);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && Date.now() - asked >= MIN_GAP) ask();
    });
  }

  // ------------------------------------------------------------ pickers
  // STM's own pop-up for every time, date and dropdown box -- and the
  // number boxes given choices (data-options) -- in place of the browser's
  // or the phone's: a list of choices a step apart under the box, with a
  // Custom box to type any other into; a month for a date; and each
  // dropdown as a button. The boxes still take typing on a computer; on a
  // phone the time and date boxes open only this, never the phone's own.
  // Opened by a click or tap, or Alt+Down (F4) from the keyboard.
  var PK_CHEVRON = SVG + '<path d="m6 9 6 6 6-6"/></svg>';
  var PK_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                   "October", "November", "December"];

  function pkPad(n) { return (n < 10 ? "0" : "") + n; }
  function pkClock(m) {  // 1080 -> "6:00 PM"
    var h = Math.floor(m / 60) % 24, mm = m % 60;
    return ((h % 12) || 12) + ":" + pkPad(mm) + (h < 12 ? " AM" : " PM");
  }
  // A time as typed in the Custom box: 18:05, 6:05 pm, 6pm, 605p, 1805.
  function pkParseTime(text) {
    var t = (text || "").toLowerCase().replace(/\s+/g, "").replace(".", ":");
    var m = /^(\d{1,2})(?::?(\d{2}))?(am|pm|a|p)?$/.exec(t);
    if (!m) return null;
    var h = parseInt(m[1], 10), mm = m[2] ? parseInt(m[2], 10) : 0, half = m[3];
    if (mm > 59) return null;
    if (half) {
      if (h < 1 || h > 12) return null;
      h = h % 12 + (half.charAt(0) === "p" ? 12 : 0);
    } else if (h > 23) {
      return null;
    }
    return pkPad(h) + ":" + pkPad(mm);
  }
  function pkIso(d) { return d.getFullYear() + "-" + pkPad(d.getMonth() + 1) + "-" + pkPad(d.getDate()); }
  function pkDate(value) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
    return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
  }

  function initPickers() {
    var coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
    var pop = document.createElement("div");
    pop.className = "pk-pop";
    pop.id = "pk-pop";
    pop.hidden = true;
    document.body.appendChild(pop);
    var field = null, kind = null, month = null, uid = 0;

    function isTime(el) { return el.matches && el.matches('input[type="time"]'); }
    function isDate(el) { return el.matches && el.matches('input[type="date"]'); }
    function hasList(el) { return el.matches && el.matches("input[data-options]"); }
    function pickable(el) { return el && !el.disabled && (isTime(el) || isDate(el) || hasList(el)); }

    // A box's choices: [value, words].
    function choices(el) {
      if (el.tagName === "SELECT") {
        return [].map.call(el.options, function (o) { return [o.value, o.textContent]; });
      }
      if (isTime(el)) {
        var step = parseInt(el.dataset.step || "15", 10) || 15, out = [];
        for (var m = 0; m < 1440; m += step) out.push([pkPad(Math.floor(m / 60)) + ":" + pkPad(m % 60), pkClock(m)]);
        return out;
      }
      var unit = el.dataset.unit || "";
      return el.dataset.options.split(",").map(function (v) { v = v.trim(); return [v, v + unit]; });
    }
    // Where the list starts: the box's value, or the nearest time to it.
    function nearest(el, list) {
      var at = list.map(function (c) { return c[0]; }).indexOf(el.value);
      if (at >= 0 || !isTime(el)) return at;
      var want = /^\d\d:\d\d/.test(el.value) ? +el.value.slice(0, 2) * 60 + +el.value.slice(3, 5) : 9 * 60;
      var best = 0;
      list.forEach(function (c, i) {
        var m = +c[0].slice(0, 2) * 60 + +c[0].slice(3, 5);
        if (Math.abs(m - want) < Math.abs(+list[best][0].slice(0, 2) * 60 + +list[best][0].slice(3, 5) - want)) best = i;
      });
      return best;
    }

    function place() {
      if (!field) return;
      var r = field.getBoundingClientRect();
      if (r.bottom < 0 || r.top > window.innerHeight) { close(false); return; }
      var w = Math.min(window.innerWidth - 16, Math.max(r.width, kind === "date" ? 296 : 200));
      pop.style.width = w + "px";
      pop.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) + "px";
      var below = window.innerHeight - r.bottom, h = pop.offsetHeight;
      var up = below < h + 12 && r.top > below;
      pop.style.top = (up ? Math.max(8, r.top - h - 6) : r.bottom + 6) + "px";
    }
    function setValue(value) {
      if (field.tagName === "SELECT" || field.pkSelect) {
        var sel = field.pkSelect || field;
        sel.value = value;
        sel.dispatchEvent(new Event("change", { bubbles: true }));
        return;
      }
      field.value = value;
      field.dispatchEvent(new Event("input", { bubbles: true }));
      field.dispatchEvent(new Event("change", { bubbles: true }));
    }
    function close(refocus) {
      if (!field) return;
      var was = field;
      field.setAttribute("aria-expanded", "false");
      pop.hidden = true;
      pop.innerHTML = "";
      field = kind = null;
      if (refocus) was.focus();
    }

    // -- a list, with a Custom box for times and numbers
    function openList(el, focusList) {
      var list = choices(el.pkSelect || el), at = nearest(el.pkSelect || el, list);
      kind = "list";
      pop.classList.remove("is-date");
      pop.innerHTML = "";
      pop.setAttribute("role", "dialog");
      pop.setAttribute("aria-label", el.pkLabel || el.getAttribute("aria-label") || "Choose");
      if (!el.pkSelect) {
        var custom = document.createElement("div");
        custom.className = "pk-custom";
        custom.innerHTML = '<label for="pk-type">Custom</label><input id="pk-type" type="text" autocomplete="off" ' +
          'enterkeyhint="done"><p class="pk-err" role="alert"></p>';
        var box = custom.querySelector("input");
        box.placeholder = isTime(el) ? "6:05 PM" : "Type it";
        box.inputMode = isTime(el) ? "text" : "decimal";
        pop.appendChild(custom);
      }
      var ul = document.createElement("ul");
      ul.className = "pk-list";
      ul.setAttribute("role", "listbox");
      ul.id = "pk-list";
      list.forEach(function (c, i) {
        var li = document.createElement("li");
        li.setAttribute("role", "option");
        li.id = "pk-o" + (++uid);
        li.tabIndex = -1;
        li.dataset.value = c[0];
        li.textContent = c[1];
        if (i === at) { li.setAttribute("aria-selected", "true"); li.classList.add("is-on"); }
        ul.appendChild(li);
      });
      pop.appendChild(ul);
      pop.hidden = false;
      place();
      var on = ul.children[Math.max(0, at)];
      if (on) ul.scrollTop = on.offsetTop - ul.clientHeight / 2 + on.offsetHeight / 2;
      if (focusList && on) on.focus();
    }
    function chooseOption(li) {
      setValue(li.dataset.value);
      close(true);
    }
    function applyCustom() {
      var box = pop.querySelector("#pk-type"), err = pop.querySelector(".pk-err");
      var text = box.value.trim(), value = null;
      if (isTime(field)) {
        value = pkParseTime(text);
      } else if (text !== "") {
        var n = field.type === "number" ? parseFloat(text) : null;
        if (field.type !== "number") value = text;
        else if (isFinite(n) && !(field.min !== "" && n < +field.min) && !(field.max !== "" && n > +field.max)) {
          value = String(n);
        }
      }
      if (value === null) {
        err.textContent = isTime(field) ? "Not a time: try 6:05 PM or 18:05." : "Not a number that fits here.";
        box.setAttribute("aria-invalid", "true");
        return;
      }
      setValue(value);
      close(true);
    }

    // -- a month, for a date
    function openDate(el, focusDay) {
      kind = "date";
      pop.classList.add("is-date");
      var chosen = pkDate(el.value);
      month = month || chosen || new Date();
      month = new Date(month.getFullYear(), month.getMonth(), 1);
      drawMonth(chosen);
      pop.hidden = false;
      place();
      if (focusDay) (pop.querySelector('.pk-day[tabindex="0"]') || pop.querySelector(".pk-day")).focus();
    }
    function drawMonth(chosen, focusIso) {
      var el = field, min = pkDate(el.min), max = pkDate(el.max), today = pkIso(new Date());
      chosen = chosen || pkDate(el.value);
      pop.setAttribute("role", "dialog");
      pop.setAttribute("aria-label", "Choose a date");
      var y = month.getFullYear(), m = month.getMonth();
      var html = '<div class="pk-month"><button type="button" class="pk-nav" data-step="-1" aria-label="Previous month">' +
        SVG + '<path d="m15 5-7 7 7 7"/></svg></button><p class="pk-title" aria-live="polite">' + PK_MONTHS[m] + " " + y +
        '</p><button type="button" class="pk-nav" data-step="1" aria-label="Next month">' + SVG +
        '<path d="m9 5 7 7-7 7"/></svg></button></div><div class="pk-week" aria-hidden="true">' +
        "<span>Mo</span><span>Tu</span><span>We</span><span>Th</span><span>Fr</span><span>Sa</span><span>Su</span></div>" +
        '<div class="pk-days">';
      var first = new Date(y, m, 1), lead = (first.getDay() + 6) % 7, days = new Date(y, m + 1, 0).getDate();
      var focusOn = focusIso || (chosen && chosen.getMonth() === m && chosen.getFullYear() === y ? pkIso(chosen) : null);
      if (!focusOn) focusOn = pkIso(new Date(y, m, Math.min(new Date().getDate(), days)));
      for (var i = 0; i < lead; i++) html += '<span class="pk-blank"></span>';
      for (var d = 1; d <= days; d++) {
        var day = new Date(y, m, d), iso = pkIso(day);
        var off = (min && day < min) || (max && day > max);
        html += '<button type="button" class="pk-day' + (iso === today ? " is-today" : "") +
          (chosen && iso === pkIso(chosen) ? " is-on" : "") + '" data-date="' + iso + '" tabindex="' +
          (iso === focusOn ? "0" : "-1") + '"' + (off ? " disabled" : "") + ' aria-label="' +
          day.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" }) + '"' +
          (chosen && iso === pkIso(chosen) ? ' aria-pressed="true"' : "") + ">" + d + "</button>";
      }
      html += '</div><div class="pk-foot"><button type="button" class="btn btn-ghost btn-sm pk-today">Today</button></div>';
      pop.innerHTML = html;
    }
    function moveDay(from, by) {
      var d = pkDate(from.dataset.date);
      d.setDate(d.getDate() + by);
      if (d.getMonth() !== month.getMonth() || d.getFullYear() !== month.getFullYear()) {
        month = new Date(d.getFullYear(), d.getMonth(), 1);
        drawMonth(null, pkIso(d));
        place();
      }
      var to = pop.querySelector('[data-date="' + pkIso(d) + '"]');
      if (to) {
        pop.querySelectorAll(".pk-day").forEach(function (b) { b.tabIndex = -1; });
        to.tabIndex = 0;
        to.focus();
      }
    }

    function open(el, byKeys) {
      if (field === el) return;
      close(false);
      field = el;
      month = null;
      el.setAttribute("aria-expanded", "true");
      (el.closest("dialog[open]") || document.body).appendChild(pop);
      if (isDate(el)) openDate(el, byKeys); else openList(el, byKeys || !!el.pkSelect);
    }

    // Phones: the time and date boxes open only STM's picker.
    function readyBox(el) {
      if (el.pkReady || !pickable(el)) return;
      el.pkReady = true;
      el.setAttribute("aria-haspopup", "dialog");
      el.setAttribute("aria-expanded", "false");
      if (coarse && (isTime(el) || isDate(el))) el.readOnly = true;
    }
    // Each dropdown: a button in its place, showing its choice; the
    // dropdown itself stays, hidden, for the form to send.
    function readySelect(sel) {
      if (sel.pkButton || sel.multiple) return;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "pk-select";
      btn.id = (sel.id || "pk-s" + (++uid)) + "-pick";
      btn.setAttribute("aria-haspopup", "listbox");
      btn.setAttribute("aria-expanded", "false");
      btn.innerHTML = '<span class="pk-select-text"></span>' + PK_CHEVRON;
      var text = btn.querySelector(".pk-select-text");
      text.id = btn.id + "-text";
      var label = sel.id && document.querySelector('label[for="' + sel.id + '"]');
      if (label) {
        label.id = label.id || sel.id + "-label";
        btn.setAttribute("aria-labelledby", label.id + " " + text.id);
        btn.pkLabel = label.textContent.trim();
        label.addEventListener("click", function (e) { e.preventDefault(); btn.focus(); });
      } else {
        btn.pkLabel = sel.getAttribute("aria-label") || "Choose";
        btn.setAttribute("aria-label", btn.pkLabel + ": " + (sel.selectedOptions[0] || {}).textContent);
      }
      function sync() {
        var o = sel.selectedOptions[0];
        text.textContent = o ? o.textContent : "";
        if (!label) btn.setAttribute("aria-label", btn.pkLabel + ": " + text.textContent);
      }
      sel.addEventListener("change", sync);
      sel.pkButton = btn;
      btn.pkSelect = sel;
      sel.hidden = true;
      sel.parentNode.insertBefore(btn, sel.nextSibling);
      sync();
    }
    document.querySelectorAll("select").forEach(readySelect);
    document.querySelectorAll('input[type="time"], input[type="date"], input[data-options]').forEach(readyBox);
    document.addEventListener("focusin", function (e) { if (pickable(e.target)) readyBox(e.target); });

    document.addEventListener("click", function (e) {
      var el = e.target.closest && e.target.closest('input[type="time"], input[type="date"], input[data-options], .pk-select');
      if (el && (el.pkSelect || pickable(el))) {
        readyBox(el);
        if (el.pkSelect && field === el) { close(true); return; }
        open(el, false);
      }
    });
    document.addEventListener("pointerdown", function (e) {
      if (field && !pop.contains(e.target) && e.target !== field) close(false);
    }, true);
    document.addEventListener("keydown", function (e) {
      var el = e.target;
      // From a box: Alt+Down or F4 opens; Down opens a dropdown too.
      if (!pop.contains(el)) {
        var openKey = (e.altKey && e.key === "ArrowDown") || e.key === "F4" ||
          (el.pkSelect && (e.key === "ArrowDown" || e.key === "ArrowUp"));
        if (openKey && (el.pkSelect || pickable(el))) {
          e.preventDefault();
          e.stopImmediatePropagation();
          readyBox(el);
          open(el, true);
        } else if (field === el && e.key === "ArrowDown") {
          // Open already, from a click: Down goes into it.
          e.preventDefault();
          e.stopImmediatePropagation();
          var into = pop.querySelector('.pk-list .is-on, .pk-day[tabindex="0"]') ||
            pop.querySelector('.pk-list li, .pk-day');
          if (into) into.focus();
        } else if (field === el && (e.key === "Escape" || e.key === "Tab")) {
          if (e.key === "Escape") e.preventDefault();
          close(false);
        }
        return;
      }
      if (e.key === "Escape" || e.key === "Tab") { e.preventDefault(); close(true); return; }
      if (el.id === "pk-type") {
        if (e.key === "Enter") { e.preventDefault(); applyCustom(); }
        else if (e.key === "ArrowDown") {
          e.preventDefault();
          (pop.querySelector(".pk-list .is-on") || pop.querySelector(".pk-list li")).focus();
        }
        return;
      }
      if (el.getAttribute("role") === "option") {
        var opts = [].slice.call(pop.querySelectorAll('[role="option"]')), i = opts.indexOf(el), to = null;
        if (e.key === "ArrowDown") to = opts[i + 1];
        else if (e.key === "ArrowUp") to = opts[i - 1] || pop.querySelector("#pk-type");
        else if (e.key === "Home") to = opts[0];
        else if (e.key === "End") to = opts[opts.length - 1];
        else if (e.key === "PageDown") to = opts[Math.min(opts.length - 1, i + 8)];
        else if (e.key === "PageUp") to = opts[Math.max(0, i - 8)];
        else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); chooseOption(el); return; }
        else return;
        e.preventDefault();
        if (to) to.focus();
        return;
      }
      if (el.classList.contains("pk-day")) {
        var by = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[e.key];
        if (by) { e.preventDefault(); moveDay(el, by); return; }
        if (e.key === "PageUp" || e.key === "PageDown") {
          e.preventDefault();
          var d = pkDate(el.dataset.date);
          d.setMonth(d.getMonth() + (e.key === "PageUp" ? -1 : 1));
          moveDay({ dataset: { date: pkIso(d) } }, 0);
        }
      }
    }, true);
    pop.addEventListener("click", function (e) {
      var opt = e.target.closest('[role="option"]');
      if (opt) { chooseOption(opt); return; }
      var day = e.target.closest(".pk-day");
      if (day && !day.disabled) { setValue(day.dataset.date); close(true); return; }
      var nav = e.target.closest(".pk-nav");
      if (nav) {
        month = new Date(month.getFullYear(), month.getMonth() + parseInt(nav.dataset.step, 10), 1);
        drawMonth();
        place();
        pop.querySelector('.pk-nav[data-step="' + nav.dataset.step + '"]').focus();
        return;
      }
      if (e.target.closest(".pk-today")) {
        var today = new Date();
        if (!pop.querySelector('[data-date="' + pkIso(today) + '"][disabled]')) { setValue(pkIso(today)); close(true); }
      }
    });
    pop.addEventListener("input", function (e) {
      if (e.target.id === "pk-type") {
        e.target.removeAttribute("aria-invalid");
        pop.querySelector(".pk-err").textContent = "";
      }
    });
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
  }

  // ------------------------------------------------------------ idle
  // While the timer runs, a stretch with no pointer or key activity on any
  // STM page is noticed when activity comes back; past the wait set in
  // Settings it asks what to do with it. It can't see work in other
  // programs -- only this browser's STM pages.
  function initIdle() {
    var dialog = document.getElementById("idle-dialog");
    if (!dialog || !dialog.showModal || !window.fetch) return;
    var wait = parseInt(dialog.dataset.minutes, 10) * 60000;
    var timerFrom = Date.now() - parseInt(dialog.dataset.elapsed, 10) * 1000;
    var status = dialog.querySelector(".idle-status");
    var KEY = "stm-active", last = 0, saved = 0, away = 0;

    function shared() {
      try { return parseInt(localStorage.getItem(KEY) || "0", 10) || 0; } catch (e) { return 0; }
    }
    function words(ms) {
      var m = Math.round(ms / 60000), h = Math.floor(m / 60);
      m %= 60;
      return (h ? h + (h === 1 ? " hour" : " hours") : "") + (h && m ? " " : "") +
        (m || !h ? m + (m === 1 ? " minute" : " minutes") : "");
    }
    function active() {
      if (dialog.open) return;
      var now = Date.now();
      // The last activity on any STM page, and never before the timer began.
      var since = Math.max(last, shared(), timerFrom);
      if (since && now - since >= wait) {
        away = now - since;
        dialog.querySelector("[data-idle-away]").textContent = words(away);
        status.textContent = "";
        dialog.showModal();
        dialog.querySelector('[data-idle-act="keep"]').focus();
      }
      last = now;
      if (now - saved > 5000) {
        saved = now;
        try { localStorage.setItem(KEY, String(now)); } catch (e) { /* storage blocked */ }
      }
    }
    // A page opened counts as coming back too.
    last = shared() ? 0 : Date.now();
    active();
    ["pointerdown", "pointermove", "keydown", "wheel", "touchstart"].forEach(function (name) {
      document.addEventListener(name, active, { passive: true, capture: true });
    });
    document.addEventListener("visibilitychange", function () { if (!document.hidden) active(); });

    dialog.addEventListener("click", function (e) {
      var button = e.target.closest("[data-idle-act]");
      if (!button) return;
      var act = button.dataset.idleAct;
      if (act === "keep") { dialog.close(); last = Date.now(); return; }
      var body = new FormData();
      body.append("action", act);
      body.append("away", String(Math.round(away / 1000)));
      fetch(dialog.dataset.url, { method: "POST", body: body, credentials: "same-origin",
                                  headers: { Accept: "application/json" } })
        .then(function (r) { if (!r.ok) throw new Error("not saved"); return r.json(); })
        .then(function (done) {
          if (done.ok) { window.location.reload(); return; }
          status.textContent = done.message;
        })
        .catch(function () { status.textContent = "Couldn't save that. Try again."; });
    });
    dialog.addEventListener("close", function () { last = Date.now(); });
  }

  // Settings: browser notifications, asked for only when this is pressed.
  function initNotifySetting() {
    var row = document.querySelector(".notify-row");
    if (!row || !("Notification" in window)) return;
    var state = row.querySelector(".notify-state");
    var button = row.querySelector("[data-notify]");
    row.hidden = false;
    function draw() {
      var on = notifying();
      if (Notification.permission === "denied") {
        state.textContent = "Notifications blocked in this browser";
        button.hidden = true;
        return;
      }
      state.textContent = on ? "Browser notifications on" : "Browser notifications";
      button.textContent = on ? "Turn off" : "Turn on";
    }
    button.addEventListener("click", function () {
      if (notifying()) {
        try { localStorage.setItem("stm-notify", "0"); } catch (e) { /* storage blocked */ }
        draw();
        return;
      }
      Promise.resolve(Notification.requestPermission()).then(function (answer) {
        if (answer === "granted") {
          try { localStorage.setItem("stm-notify", "1"); } catch (e) { /* storage blocked */ }
        }
        draw();
      });
    });
    draw();
  }

  // ------------------------------------------------------------ the year
  // The heatmap: scrolled to now on a narrow screen; the day under the
  // pointer (or focused) in words below it; and one stop for Tab, with the
  // arrow keys moving a day (Up, Down) or a week (Left, Right).
  function initHeatmap() {
    document.querySelectorAll("[data-hm]").forEach(function (grid) {
      var card = grid.closest(".hm-card");
      var read = card.querySelector(".hm-read");
      var scroll = card.querySelector(".hm-scroll");
      var cells = [].slice.call(grid.children);
      if (scroll) scroll.scrollLeft = scroll.scrollWidth;
      function show(e) {
        var day = e.target.closest && e.target.closest("a.hm-day");
        if (day && read) read.textContent = day.getAttribute("aria-label");
      }
      grid.addEventListener("mouseover", show);
      grid.addEventListener("focusin", show);
      grid.addEventListener("mouseleave", function () {
        if (read && !grid.contains(document.activeElement)) read.textContent = read.dataset.rest;
      });
      grid.addEventListener("keydown", function (e) {
        var steps = { ArrowUp: -1, ArrowDown: 1, ArrowLeft: -7, ArrowRight: 7 };
        var at = cells.indexOf(e.target);
        if (at < 0) return;
        var to = null;
        if (e.key in steps) to = cells[at + steps[e.key]];
        else if (e.key === "Home") to = grid.querySelector("a.hm-day");
        else if (e.key === "End") to = [].slice.call(grid.querySelectorAll("a.hm-day")).pop();
        else return;
        e.preventDefault();
        // The edge, a day not reached, or one hidden (a phone's shorter year).
        if (!to || to.tagName !== "A" || !to.offsetParent) return;
        e.target.tabIndex = -1;
        to.tabIndex = 0;
        to.focus();
      });
    });
  }

  // ------------------------------------------------------------ arrange
  // Home's cards in a list: tick to show, Up and Down (or drag) to put in
  // order. Home follows straight away and the layout saves itself.
  function initArrange() {
    var panel = document.getElementById("dash-arrange");
    var open = document.querySelector(".dash-arrange-open button");
    var grid = document.querySelector(".dash-grid");
    if (!panel || !open || !grid || !window.fetch) return;
    var list = panel.querySelector(".arr-list");
    var status = document.getElementById("arr-status");
    var timer = null, dragging = null;
    open.hidden = false;

    function items() { return [].slice.call(list.children); }
    function nameOf(item) { return item.querySelector(".arr-show span").textContent; }
    function refresh() {
      var all = items();
      all.forEach(function (item, i) {
        item.querySelector(".arr-up").setAttribute("aria-disabled", i === 0 ? "true" : "false");
        item.querySelector(".arr-down").setAttribute("aria-disabled", i === all.length - 1 ? "true" : "false");
      });
    }
    // Home as the list says: each card in its place, shown or not.
    function apply() {
      items().forEach(function (item) {
        var card = grid.querySelector(':scope > [data-card="' + item.dataset.card + '"]');
        if (!card) return;  // nothing to show today (Saturday plan, To fill in)
        card.hidden = !item.querySelector("input").checked;
        grid.appendChild(card);
      });
      refresh();
      clearTimeout(timer);
      timer = setTimeout(save, 400);
    }
    function save() {
      var body = new FormData();
      items().forEach(function (item) {
        body.append("order", item.dataset.card);
        if (!item.querySelector("input").checked) body.append("hidden", item.dataset.card);
      });
      fetch(panel.dataset.url, { method: "POST", body: body, credentials: "same-origin",
                                 headers: { "Accept": "application/json" } })
        .then(function (r) { if (!r.ok) throw new Error("not saved"); status.textContent = "Saved."; })
        .catch(function () { status.textContent = "Couldn't save that. Try again."; });
    }
    function slide(item) {
      item.classList.remove("is-moved");
      void item.offsetWidth;
      item.classList.add("is-moved");
    }
    function move(item, by) {
      var all = items(), to = all.indexOf(item) + by;
      if (to < 0 || to >= all.length) return false;
      list.insertBefore(item, by < 0 ? all[to] : all[to].nextSibling);
      slide(item);
      status.textContent = nameOf(item) + ", " + (to + 1) + " of " + all.length + ".";
      apply();
      return true;
    }
    function show(on) {
      panel.hidden = !on;
      open.setAttribute("aria-expanded", on ? "true" : "false");
      open.closest(".dash-arrange-open").hidden = on;
      if (on) {
        panel.querySelector(".card-title").focus();
        panel.scrollIntoView({ block: "nearest" });
      } else {
        open.focus();
      }
    }

    open.addEventListener("click", function () { show(true); });
    panel.querySelector(".arr-done").addEventListener("click", function () { show(false); });
    panel.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.preventDefault(); show(false); }
    });
    panel.querySelector(".arr-reset").addEventListener("click", function () {
      JSON.parse(panel.dataset.default).forEach(function (key) {
        var item = list.querySelector('[data-card="' + key + '"]');
        if (item) { item.querySelector("input").checked = true; list.appendChild(item); }
      });
      status.textContent = "Back to the usual layout.";
      apply();
    });
    list.addEventListener("change", apply);
    list.addEventListener("click", function (e) {
      var button = e.target.closest(".arr-up, .arr-down");
      if (!button || button.getAttribute("aria-disabled") === "true") return;
      move(button.closest(".arr-item"), button.classList.contains("arr-up") ? -1 : 1);
      // Kept on the same button as the line moves -- or its pair, at an end.
      var pair = button.getAttribute("aria-disabled") === "true"
        ? button.closest(".arr-item").querySelector(button.classList.contains("arr-up") ? ".arr-down" : ".arr-up")
        : button;
      pair.focus();
    });
    // Drag with a mouse; Up and Down do the same on a phone or keyboard.
    list.addEventListener("dragstart", function (e) {
      dragging = e.target.closest(".arr-item");
      if (!dragging) return;
      dragging.classList.add("is-dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", dragging.dataset.card);
    });
    list.addEventListener("dragover", function (e) {
      var over = e.target.closest(".arr-item");
      if (!dragging || !over) return;
      e.preventDefault();
      items().forEach(function (item) { item.classList.toggle("is-over", item === over && item !== dragging); });
    });
    list.addEventListener("drop", function (e) {
      var over = e.target.closest(".arr-item");
      if (!dragging || !over || over === dragging) return;
      e.preventDefault();
      var all = items();
      list.insertBefore(dragging, all.indexOf(dragging) < all.indexOf(over) ? over.nextSibling : over);
      slide(dragging);
      apply();
    });
    list.addEventListener("dragend", function () {
      if (dragging) dragging.classList.remove("is-dragging");
      dragging = null;
      items().forEach(function (item) { item.classList.remove("is-over"); });
    });
    refresh();
  }

  // ------------------------------------------------------------ calendar grid
  // A block opens in a box to change its words or times (an empty one, or
  // a click on the grid, to write a new line); a written line's bottom edge
  // can be dragged, in 5-minute steps, and is saved on letting go.
  function initCalendarGrid() {
    var grid = document.querySelector(".cg");
    var dialog = document.getElementById("cg-dialog");
    if (!grid || !dialog || !dialog.showModal) return;
    var form = document.getElementById("cg-form");
    var resize = document.getElementById("cg-resize");
    var from = parseInt(grid.dataset.from, 10), to = parseInt(grid.dataset.to, 10);
    var ppm = function () { return grid.querySelector(".cg-body").getBoundingClientRect().height / (to - from); };
    function hhmm(m) { m = ((m % 1440) + 1440) % 1440; return String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0"); }
    function set(f, name, value) { f.querySelector('[name="' + name + '"]').value = value; }

    function open(date, block, start, end) {
      set(form, "date", date);
      set(form, "id", block ? block.dataset.id : "");
      set(form, "start", start);
      set(form, "end", end);
      set(form, "text", block ? block.dataset.text : "");
      var written = block && block.dataset.id;
      document.getElementById("cg-dialog-title").textContent = written ? "Change this line" : "Write a line";
      // More: the line menu, for a line with words.
      var more = document.getElementById("cg-more");
      if (more) more.hidden = !(block && block.dataset.copy);
      dialog.stmBlock = block || null;
      document.getElementById("cg-open").href = "/timesheet/" + date;
      dialog.showModal();
      document.getElementById("cg-text").focus();
    }
    document.getElementById("cg-cancel").addEventListener("click", function () { dialog.close(); });
    // On its way: the box steps aside so the loading circle shows.
    form.addEventListener("submit", function (e) {
      setTimeout(function () { if (!e.defaultPrevented) dialog.close(); }, 0);
    });

    // Zoom: how fine the grid is -- a slot of an hour, half an hour, 15 or 5
    // minutes -- and the step a drag snaps to and the arrows move by. Kept
    // in the browser; the page's head puts it on before the grid is drawn.
    var root = document.documentElement;
    var zooms = document.querySelectorAll(".cg-zoom-btn");
    var status = document.getElementById("cg-status");
    function step() { return parseInt(root.getAttribute("data-cal-zoom") || "60", 10); }
    function say(words) { if (status) status.textContent = words; }
    function showZoom() {
      zooms.forEach(function (b) { b.setAttribute("aria-pressed", String(parseInt(b.dataset.zoom, 10) === step())); });
    }
    zooms.forEach(function (b) {
      b.addEventListener("click", function () {
        if (b.dataset.zoom === "60") root.removeAttribute("data-cal-zoom");
        else root.setAttribute("data-cal-zoom", b.dataset.zoom);
        try { localStorage.setItem("stm-cal-zoom", b.dataset.zoom); } catch (e) { /* not kept - still applied */ }
        showZoom();
        if (nowLine && !nowLine.hidden) nowLine.scrollIntoView({ block: "center" });
      });
    });
    showZoom();

    // Now: a line across today's column, moved on as the minutes pass.
    var nowLine = grid.querySelector(".cg-now[data-now]");
    var loaded = Date.now();
    function placeNow() {
      var at = parseInt(nowLine.dataset.now, 10) + Math.floor((Date.now() - loaded) / 60000);
      nowLine.style.setProperty("--s", at - from);
      nowLine.hidden = !(at >= from && at < to);
    }
    if (nowLine) {
      placeNow();
      setInterval(placeNow, 30000);
    }

    // A written line's times, shown as they change and then saved: the
    // lines either side follow, as on the Timesheet (POST /calendar/line).
    function show(block, a, b) {
      block.style.setProperty("--s", a - from);
      block.style.setProperty("--l", b - a);
      var label = block.querySelector(".cg-time");
      if (label) label.textContent = clock12(hhmm(a)) + " \u2013 " + clock12(hhmm(b));
    }
    function save(block, a, b) {
      set(resize, "date", block.closest(".cg-col").dataset.date);
      set(resize, "id", block.dataset.id);
      set(resize, "start", hhmm(a));
      set(resize, "end", hhmm(b));
      set(resize, "text", block.dataset.text);
      try { sessionStorage.setItem("stm-cal-focus", block.dataset.id); } catch (e) { /* focus not kept */ }
      if (resize.requestSubmit) resize.requestSubmit(); else resize.submit();
    }

    var dragged = false;
    grid.addEventListener("click", function (e) {
      // A phone only looks: a line's link opens its day on the Timesheet,
      // and the empty part of the day does nothing.
      if (onPhone()) {
        if (!e.target.closest(".cg-block[href]")) e.preventDefault();
        return;
      }
      if (dragged) { e.preventDefault(); dragged = false; return; }
      var col = e.target.closest(".cg-col");
      if (!col) return;
      var block = e.target.closest(".cg-block");
      e.preventDefault();
      if (block) return open(col.dataset.date, block, block.dataset.start, block.dataset.end);
      // An empty part of the day: an hour from the slot clicked.
      var y = e.clientY - col.getBoundingClientRect().top;
      var at = Math.max(from, Math.min(to - 60, Math.floor((from + y / ppm()) / step()) * step()));
      open(col.dataset.date, null, hhmm(at), hhmm(at + 60));
    });

    // Dragging: the bottom edge moves the end, the top edge the start, and
    // the line itself (with a mouse or pen) moves the whole of it, snapping
    // to the zoom. A press that hardly moves is a click: the line's box opens.
    grid.addEventListener("pointerdown", function (e) {
      if (e.button !== 0 || onPhone()) return;
      var block = e.target.closest('.cg-block[aria-describedby="cg-keys"]');
      if (!block) return;
      var grip = e.target.closest(".cg-grip");
      var mode = grip ? (grip.classList.contains("cg-grip-start") ? "start" : "end") : "move";
      if (mode === "move" && e.pointerType === "touch") return;
      if (grip) e.preventDefault();
      var a0 = parseInt(block.dataset.a, 10), b0 = parseInt(block.dataset.b, 10), a = a0, b = b0;
      var y0 = e.clientY, scale = ppm(), slot = step(), moved = false;
      var held = grip || block;
      held.setPointerCapture(e.pointerId);
      function snap(m) { return Math.round(m / slot) * slot; }
      function move(ev) {
        if (!moved && Math.abs(ev.clientY - y0) < 4) return;
        if (!moved) {
          moved = true;
          block.classList.add("is-dragging");
        }
        var d = (ev.clientY - y0) / scale;
        if (mode === "end") b = Math.max(a + slot, Math.min(to, snap(b0 + d)));
        else if (mode === "start") a = Math.min(b - slot, Math.max(from, snap(a0 + d)));
        else {
          a = Math.max(from, Math.min(to - (b0 - a0), snap(a0 + d)));
          b = a + (b0 - a0);
        }
        show(block, a, b);
      }
      function up() {
        held.removeEventListener("pointermove", move);
        held.removeEventListener("pointerup", up);
        held.removeEventListener("pointercancel", up);
        block.classList.remove("is-dragging");
        if (!moved) return;
        dragged = true;
        setTimeout(function () { dragged = false; }, 0);
        if (a !== a0 || b !== b0) save(block, a, b);
      }
      held.addEventListener("pointermove", move);
      held.addEventListener("pointerup", up);
      held.addEventListener("pointercancel", up);
    });

    // The keyboard's drag: Up and Down move a written line by the zoom
    // step, with Shift its end; it saves once the keys stop, and Esc puts it
    // back. After the save the page comes back on the same line.
    var keyed = null, keyA, keyB, keyA0, keyB0, waiting = null;
    grid.addEventListener("keydown", function (e) {
      var block = e.target.closest && e.target.closest('.cg-block[aria-describedby="cg-keys"]');
      if (!block || onPhone()) return;
      if (e.key === "Escape" && keyed === block) {
        e.preventDefault();
        clearTimeout(waiting);
        show(block, keyA0, keyB0);
        keyed = null;
        say("Put back.");
        return;
      }
      if ((e.key !== "ArrowUp" && e.key !== "ArrowDown") || e.altKey || e.ctrlKey || e.metaKey) return;
      e.preventDefault();
      if (keyed !== block) {
        keyed = block;
        keyA = keyA0 = parseInt(block.dataset.a, 10);
        keyB = keyB0 = parseInt(block.dataset.b, 10);
      }
      var by = step() * (e.key === "ArrowUp" ? -1 : 1);
      if (e.shiftKey) keyB = Math.max(keyA + step(), Math.min(to, keyB + by));
      else {
        var length = keyB - keyA;
        keyA = Math.max(from, Math.min(to - length, keyA + by));
        keyB = keyA + length;
      }
      show(block, keyA, keyB);
      say(clock12(hhmm(keyA)) + " to " + clock12(hhmm(keyB)));
      clearTimeout(waiting);
      waiting = setTimeout(function () {
        if (keyed === block && (keyA !== keyA0 || keyB !== keyB0)) save(block, keyA, keyB);
        keyed = null;
      }, 900);
    });
    try {
      var back = sessionStorage.getItem("stm-cal-focus");
      sessionStorage.removeItem("stm-cal-focus");
      var again = back && grid.querySelector('.cg-block[data-id="' + back + '"]');
      if (again) again.focus();
    } catch (e) { /* no storage: the page starts at the top */ }
  }

  // ------------------------------------------------------------ timer
  // The running timer's clock, counting on from the seconds the page was
  // given, so it stays right however long the page is left open.
  // One clock for every place the running timer shows -- the card on Home
  // and the Timesheet, the bar at the top, the pill on a phone.
  function initTimerClock() {
    var source = document.querySelector("[data-elapsed]");
    var clocks = document.querySelectorAll(".timer-clock");
    if (!source || !clocks.length) return;
    var started = Date.now() - parseInt(source.dataset.elapsed, 10) * 1000;
    function pad(n) { return (n < 10 ? "0" : "") + n; }
    function tick() {
      var s = Math.max(0, Math.floor((Date.now() - started) / 1000));
      var text = Math.floor(s / 3600) + ":" + pad(Math.floor(s / 60) % 60) + ":" + pad(s % 60);
      clocks.forEach(function (clock) { clock.textContent = text; });
    }
    tick();
    setInterval(tick, 1000);
  }

  // -------------------------------------------------------------- palette
  // Ctrl+K -- or / when no box has the cursor -- opens a search box over
  // everything there is to do: go to a page or a day, Login, Break, Back or
  // Logout as the day stands, start or stop the timer, start one of the last
  // ten tasks again, dark mode. Arrows move, Enter chooses, Esc closes, and
  // how many results there are is read out.
  var MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
  var DAY_WORDS = /^(last )?(mon|monday|tue|tues|tuesday|wed|weds|wednesday|thu|thur|thurs|thursday|fri|friday|sat|saturday|sun|sunday)$/;

  function isoDay(d) {
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }

  // A day from what's typed: today, yesterday, friday, last friday, 12 nov,
  // nov 12, 12 november 2026, 12/11 (the day first), 2026-11-12. Never a day
  // still to come: a date with no year that hasn't come round yet is last
  // year's. todayIso is the day being worked, as the server has it.
  function paletteDay(text, todayIso) {
    var q = text.trim().toLowerCase().replace(/[.,]/g, " ").replace(/\s+/g, " ");
    var t = todayIso.split("-");
    var today = new Date(+t[0], +t[1] - 1, +t[2]);
    function back(n) { var d = new Date(today); d.setDate(d.getDate() - n); return d; }
    if (q === "today") return today;
    if (q === "yesterday") return back(1);
    var m = q.match(DAY_WORDS), y = null, mo, d;
    if (m) {
      var gap = (today.getDay() - ["sun", "mon", "tue", "wed", "thu", "fri", "sat"].indexOf(m[2].slice(0, 3)) + 7) % 7;
      return back(m[1] && !gap ? 7 : gap);  // last friday, on a Friday: a week ago
    }
    if ((m = q.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/))) { y = +m[1]; mo = +m[2] - 1; d = +m[3]; }
    else if ((m = q.match(/^(\d{1,2})[\/-](\d{1,2})(?:[\/-](\d{2}|\d{4}))?$/))) { d = +m[1]; mo = +m[2] - 1; y = m[3] ? +m[3] : null; }
    else if ((m = q.match(/^(\d{1,2})(?:st|nd|rd|th)? ([a-z]{3,})(?: (\d{4}))?$/))) { d = +m[1]; mo = MONTHS.indexOf(m[2].slice(0, 3)); y = m[3] ? +m[3] : null; }
    else if ((m = q.match(/^([a-z]{3,}) (\d{1,2})(?:st|nd|rd|th)?(?: (\d{4}))?$/))) { mo = MONTHS.indexOf(m[1].slice(0, 3)); d = +m[2]; y = m[3] ? +m[3] : null; }
    else return null;
    if (mo < 0 || mo > 11 || d < 1 || d > 31) return null;
    if (y !== null && y < 100) y += 2000;
    var day = new Date(y === null ? today.getFullYear() : y, mo, d);
    if (day.getMonth() !== mo) return null;  // 31 Feb
    if (y === null && day > today) day.setFullYear(day.getFullYear() - 1);
    return day > today ? null : day;
  }

  function initPalette() {
    var dialog = document.getElementById("palette");
    if (!dialog || typeof dialog.showModal !== "function") return;
    var input = document.getElementById("cp-input");
    var list = document.getElementById("cp-list");
    var status = document.getElementById("cp-status");
    var here = window.location.pathname + window.location.search;
    var data = null, items = [], active = 0, spoken = null;

    var pages = [].map.call(document.querySelectorAll(".side-nav a.nav-link"), function (a) {
      return { label: a.textContent.trim(), url: a.getAttribute("href") };
    });
    [["Week timesheet", "/timesheet/week"], ["Every line written", "/timesheet/all"],
     ["Calendar week", "/calendar/week"], ["Calendar day", "/calendar/day"],
     // Off the menus: found only here.
     ["Where the time went", "/reports/time"]].forEach(function (p) {
      pages.push({ label: p[0], url: p[1] });
    });

    function go(url) { return function () { window.location.href = url; }; }
    function post(url, fields) {
      return function () {
        var form = document.createElement("form");
        form.method = "post";
        form.action = url;
        Object.keys(fields).forEach(function (name) {
          var box = document.createElement("input");
          box.type = "hidden";
          box.name = name;
          box.value = fields[name];
          form.appendChild(box);
        });
        document.body.appendChild(form);
        if (form.requestSubmit) form.requestSubmit(); else form.submit();
      };
    }

    function options(text) {
      var q = text.trim(), out = [];
      var words = q.toLowerCase().split(/\s+/).filter(Boolean);
      function add(kind, label, run, also) {
        var hay = (label + " " + kind + " " + (also || "")).toLowerCase();
        if (words.every(function (w) { return hay.indexOf(w) >= 0; })) out.push({ kind: kind, label: label, run: run });
      }
      if (data && q) {
        var day = paletteDay(q, data.today);
        if (day) {
          var name = day.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
          var iso = isoDay(day);
          out.push({ kind: "Day", label: "Timesheet for " + name, run: go(iso === data.today ? "/timesheet" : "/timesheet/" + iso) });
          out.push({ kind: "Day", label: "Calendar for " + name, run: go("/calendar/day/" + iso) });
        }
      }
      if (data) {
        data.punches.forEach(function (p) { add("Punch", p.label, post(p.url, {})); });
        if (data.fix_url) add("Punch", "Put in the missing logout", go(data.fix_url));
        if (data.timer) add("Timer", "Stop the timer: " + data.timer.text, post(data.stop_url, { back: here }));
        if (data.can_time) {
          data.recent.forEach(function (task) {
            if (!data.timer || task !== data.timer.text) add("Task", "Start again: " + task, post(data.start_url, { text: task, back: here }));
          });
        }
      }
      pages.forEach(function (p) { add("Page", p.label, go(p.url)); });
      add("Setting", currentTheme() === "dark" ? "Light mode" : "Dark mode", function () {
        var toggle = document.querySelector(".js-theme-toggle");
        if (toggle) toggle.click();
      }, "dark light theme");
      out = out.slice(0, 40);
      // Words that aren't a place or a task yet: a new task to time, last.
      if (data && data.can_time && q && data.recent.indexOf(q) < 0 && (!data.timer || data.timer.text !== q)) {
        out.push({ kind: "Timer", label: "Start the timer: " + q, run: post(data.start_url, { text: q, back: here }) });
      }
      return out;
    }

    function select(i) {
      var rows = list.querySelectorAll(".cp-item");
      if (!rows.length) return;
      active = (i + rows.length) % rows.length;
      rows.forEach(function (row, n) { row.setAttribute("aria-selected", n === active ? "true" : "false"); });
      input.setAttribute("aria-activedescendant", rows[active].id);
      rows[active].scrollIntoView({ block: "nearest" });
    }
    function say(words) {
      clearTimeout(spoken);
      spoken = setTimeout(function () { status.textContent = words; }, 350);
    }
    function render() {
      items = options(input.value);
      list.innerHTML = "";
      items.forEach(function (item, i) {
        var row = document.createElement("li");
        row.className = "cp-item";
        row.id = "cp-opt-" + i;
        row.setAttribute("role", "option");
        var label = document.createElement("span");
        label.className = "cp-label";
        label.textContent = item.label;
        var kind = document.createElement("span");
        kind.className = "cp-kind";
        kind.textContent = item.kind;
        row.appendChild(label);
        row.appendChild(kind);
        row.addEventListener("pointermove", function () { if (active !== i) select(i); });
        row.addEventListener("click", function () { choose(i); });
        list.appendChild(row);
      });
      if (items.length) select(0); else input.removeAttribute("aria-activedescendant");
      say(items.length ? items.length + (items.length === 1 ? " result" : " results") : "Nothing found");
    }
    function choose(i) {
      var item = items[i];
      if (!item) return;
      dialog.close();
      item.run();
    }
    function open() {
      if (dialog.open || document.querySelector("dialog[open]")) return;
      input.value = "";
      render();
      dialog.showModal();
      input.focus();
      fetch("/api/palette", { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (got) { if (got) { data = got; if (dialog.open) render(); } })
        .catch(function () { /* the pages are still there */ });
    }

    input.addEventListener("input", render);
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); select(active + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); select(active - 1); }
      else if (e.key === "PageDown") { e.preventDefault(); select(Math.min(items.length - 1, active + 5)); }
      else if (e.key === "PageUp") { e.preventDefault(); select(Math.max(0, active - 5)); }
      else if (e.key === "Enter") { e.preventDefault(); choose(active); }
    });
    dialog.addEventListener("click", function (e) { if (e.target === dialog) dialog.close(); });
    document.querySelectorAll(".cp-open").forEach(function (b) { b.addEventListener("click", open); });
    document.addEventListener("keydown", function (e) {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        if (dialog.open) dialog.close(); else open();
        return;
      }
      var t = e.target;
      var typing = t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
      if (e.key === "/" && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        open();
      }
    });
  }

  // ------------------------------------------------------------ line menu
  // A line's menu -- on the Timesheet, a Board card, a Calendar block: Copy,
  // Mark posted (or not), Duplicate, Split in two, Delete. Opened by a right
  // click on the line (a text box keeps the browser's own menu, for
  // pasting), by its More button, or by Shift+F10 or the menu key on a line
  // with the focus. The arrows move, Enter chooses, Esc closes. Where a line
  // has a button of its own for one of these, the menu presses that.
  function initLineMenu() {
    var menu = document.createElement("div");
    menu.className = "lm";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", "Line");
    menu.hidden = true;
    document.body.appendChild(menu);
    var opener = null;
    var here = window.location.pathname + window.location.search;
    var calendar = document.getElementById("cg-dialog");

    document.querySelectorAll(".ts-more, .kb-more").forEach(function (b) { b.hidden = false; });

    function lineOf(el) {
      return el && el.closest ? el.closest(".ts-line, .kb-card[data-copy], .cg-block[data-copy]") : null;
    }
    function post(url) {
      var form = document.createElement("form");
      form.method = "post";
      form.action = url;
      var back = document.createElement("input");
      back.type = "hidden";
      back.name = "back";
      back.value = here;
      form.appendChild(back);
      document.body.appendChild(form);
      if (form.requestSubmit) form.requestSubmit(); else form.submit();
    }
    // Split in two and Duplicate on the Timesheet go with the page, saved.
    function submitLine(line, name) {
      var form = line.closest("form");
      var box = document.createElement("input");
      box.type = "hidden";
      box.name = name;
      box.value = String([].indexOf.call(line.parentNode.children, line));
      form.appendChild(box);
      if (form.requestSubmit) form.requestSubmit(); else form.submit();
      if (form.dataset.sent !== "true") box.remove();  // the save was called off
    }

    function actions(line) {
      if (line.classList.contains("ts-line")) {
        var a = clockMinutes(field(line, "start").value, "start"), b = clockMinutes(field(line, "end").value, "end");
        var writes = !!lineWords(line) && !line.classList.contains("is-break");
        var copied = line.classList.contains("is-copied");
        function marks(on) {
          return function () { line.dispatchEvent(new CustomEvent("stm:mark", { bubbles: true, detail: { on: on } })); };
        }
        return [
          { label: "Copy", ok: writes, run: function () { line.querySelector(".ts-copy").click(); } },
          copied ? { label: "Mark not posted", ok: writes, run: marks(false) } : { label: "Mark posted", ok: writes, run: marks(true) },
          { label: "Duplicate", ok: writes, run: function () { submitLine(line, "duplicate"); } },
          { label: "Split in two", ok: writes && a !== null && b !== null && b - a >= 10, run: function () { submitLine(line, "split"); } },
          { label: "Delete", ok: true, danger: true, run: function () { line.querySelector(".ts-delete").click(); } },
        ];
      }
      // A Board card or a Calendar block: one saved line, by its id.
      var url = "/line/" + line.dataset.id + "/";
      var onBoard = line.classList.contains("kb-card");
      var posted = line.classList.contains("kind-posted") || line.classList.contains("is-copied");
      var minutes = onBoard ? parseInt(line.dataset.minutes, 10) : parseInt(line.dataset.b, 10) - parseInt(line.dataset.a, 10);
      var span = onBoard ? line.dataset.span : (line.querySelector(".cg-time") || line).textContent.split(" · ")[0];
      function setMark(on) {
        return function () {
          if (onBoard) {
            line.dispatchEvent(new CustomEvent("stm:mark", { bubbles: true, detail: { on: on } }));
            return;
          }
          fetch(url + (on ? "mark" : "unmark"), { method: "POST", credentials: "same-origin", headers: { Accept: "application/json" } })
            .then(function (r) { if (!r.ok) throw new Error("not marked"); return r.json(); })
            .then(function (done) { line.classList.toggle("is-copied", !!done.copied); })
            .catch(function () { post(url + (on ? "mark" : "unmark")); });
        };
      }
      return [
        { label: "Copy", ok: true, run: function () {
          copyText(line.dataset.copy).then(posted ? function () {} : setMark(true), function () {
            window.prompt("Couldn't copy here. Copy this yourself:", line.dataset.copy);
          });
        } },
        posted ? { label: "Mark not posted", ok: true, run: setMark(false) } : { label: "Mark posted", ok: true, run: setMark(true) },
        { label: "Duplicate", ok: true, run: function () { post(url + "duplicate"); } },
        { label: "Split in two", ok: minutes >= 10, run: function () { post(url + "split"); } },
        { label: "Delete", ok: true, danger: true, run: function () {
          if (window.confirm("Delete the line from " + span + "?")) post(url + "delete");
        } },
      ];
    }

    function items() { return [].slice.call(menu.querySelectorAll('.lm-item:not([aria-disabled="true"])')); }
    function close(restore) {
      if (menu.hidden) return;
      menu.hidden = true;
      if (restore && opener && document.contains(opener)) opener.focus();
      opener = null;
    }
    function open(line, x, y, from) {
      opener = from;
      var host = (from && from.closest && from.closest("dialog[open]")) || document.body;
      if (menu.parentNode !== host) host.appendChild(menu);
      menu.innerHTML = "";
      actions(line).forEach(function (a) {
        var item = document.createElement("button");
        item.type = "button";
        item.className = "lm-item" + (a.danger ? " is-danger" : "");
        item.setAttribute("role", "menuitem");
        item.tabIndex = -1;
        item.textContent = a.label;
        if (!a.ok) item.setAttribute("aria-disabled", "true");
        item.addEventListener("click", function () {
          if (!a.ok) return;
          close(true);  // back to where it was opened from, for whatever's next
          if (calendar && calendar.open && host === calendar) calendar.close();
          a.run();
        });
        menu.appendChild(item);
      });
      menu.hidden = false;
      var w = menu.offsetWidth, h = menu.offsetHeight;
      menu.style.left = Math.max(8, Math.min(x, window.innerWidth - w - 8)) + "px";
      menu.style.top = Math.max(8, Math.min(y, window.innerHeight - h - 8)) + "px";
      var first = items()[0];
      if (first) first.focus();
    }
    function openBeside(line, el) {
      var r = el.getBoundingClientRect();
      open(line, r.left, r.bottom + 4, el);
    }

    menu.addEventListener("keydown", function (e) {
      var list = items(), at = list.indexOf(document.activeElement);
      if (e.key === "ArrowDown") { e.preventDefault(); list[(at + 1) % list.length].focus(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); list[(at - 1 + list.length) % list.length].focus(); }
      else if (e.key === "Home") { e.preventDefault(); list[0].focus(); }
      else if (e.key === "End") { e.preventDefault(); list[list.length - 1].focus(); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(true); }
      else if (e.key === "Tab") { close(false); }
    });
    document.addEventListener("pointerdown", function (e) { if (!menu.contains(e.target)) close(false); }, true);
    window.addEventListener("resize", function () { close(false); });
    document.addEventListener("scroll", function () { close(false); }, true);

    document.addEventListener("contextmenu", function (e) {
      var line = lineOf(e.target);
      if (!line || e.target.matches('input[type="text"], textarea')) return;
      if (onPhone() && !line.matches(".kb-card")) return;  // Timesheet and Calendar: look only
      e.preventDefault();
      open(line, e.clientX, e.clientY, line.querySelector(".ts-more, .kb-more") || line);
    });
    document.addEventListener("keydown", function (e) {
      if (!((e.key === "F10" && e.shiftKey) || e.key === "ContextMenu")) return;
      var at = document.activeElement;
      var line = lineOf(at) || (calendar && calendar.open && calendar.contains(at) ? calendar.stmBlock : null);
      if (!line) return;
      e.preventDefault();
      openBeside(line, at);
    });
    document.addEventListener("click", function (e) {
      var more = e.target.closest && e.target.closest(".ts-more, .kb-more, #cg-more");
      if (!more) return;
      var line = more.id === "cg-more" ? calendar && calendar.stmBlock : lineOf(more);
      if (line) openBeside(line, more);
    });
  }

  // ----------------------------------------------------------- time nudge
  // In any time box -- the Timesheet, the Calendar's box, a day's Edit
  // screen -- Up and Down move the time 5 minutes (on to the next five:
  // 7:02 goes to 7:05), with Shift an hour. It goes through as typing would,
  // so the line beside it follows and the Timesheet saves once the nudging
  // stops.
  function initTimeNudge() {
    document.addEventListener("keydown", function (e) {
      var box = e.target;
      if (!box || box.tagName !== "INPUT" || box.type !== "time") return;
      if ((e.key !== "ArrowUp" && e.key !== "ArrowDown") || e.altKey || e.ctrlKey || e.metaKey) return;
      if (!/^\d\d:\d\d/.test(box.value)) return;
      e.preventDefault();
      var m = parseInt(box.value.slice(0, 2), 10) * 60 + parseInt(box.value.slice(3, 5), 10);
      var up = e.key === "ArrowUp";
      if (e.shiftKey) m += up ? 60 : -60;
      else m = up ? Math.floor(m / 5) * 5 + 5 : Math.ceil(m / 5) * 5 - 5;
      m = ((m % 1440) + 1440) % 1440;
      box.value = String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0");
      box.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }

  // ------------------------------------------------------------ timer bar
  // On a phone the bar lives in a sheet, opened from the pill above the
  // tabs. Back on a tab left open a while: if the timer or the day has moved
  // on elsewhere since, the page is fetched afresh -- unless something's
  // being typed.
  function initTimerBar() {
    var pill = document.querySelector(".tb-pill");
    var sheet = document.getElementById("tb-sheet");
    if (pill && sheet && typeof sheet.showModal === "function") {
      pill.addEventListener("click", function () {
        sheet.showModal();
        var box = sheet.querySelector('.tb-new input[name="text"]');
        if (box) box.focus();
      });
      sheet.addEventListener("click", function (e) {
        if (e.target === sheet || (e.target.closest && e.target.closest("[data-sheet-close]"))) sheet.close();
      });
      sheet.addEventListener("submit", function () { sheet.close(); });
    }
    var bar = document.querySelector(".tb-bar");
    if (!bar || !window.fetch) return;
    var text = bar.querySelector(".tb-text");
    var shown = { state: bar.dataset.state, timer: text ? text.textContent : "" };
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState !== "visible") return;
      fetch("/api/status", { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (now) {
          if (!now) return;
          var state = now.day_state === "ready" ? "out" : now.day_state;
          var timer = now.timer ? now.timer.text : "";
          var busy = document.activeElement && /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
          if ((timer !== shown.timer || state !== shown.state) && !busy && !document.querySelector("dialog[open]")) {
            window.location.reload();
          }
        })
        .catch(function () { /* offline: the bar stays as it was */ });
    });
  }

  // ------------------------------------------------------------ page wait
  // A press that loads another page -- a form sent, a link followed --
  // puts a small spinning circle over the page until the next one arrives,
  // and nothing can be pressed or typed into meanwhile. Downloads, new tabs,
  // jumps within the page and anything the page handles itself don't. If
  // the next page never comes (a lost connection), the page comes back
  // after 20 seconds.
  var WAIT_GIVE_UP_MS = 20000;

  function initPageWait() {
    var wait = document.getElementById("page-wait");
    if (!wait) return;
    var timer = null, frozen = [];

    function show() {
      if (!wait.hidden) return;
      wait.hidden = false;
      frozen = [].filter.call(document.body.children, function (el) { return el !== wait && !el.inert; });
      frozen.forEach(function (el) { el.inert = true; });
      timer = setTimeout(hide, WAIT_GIVE_UP_MS);
    }
    function hide() {
      clearTimeout(timer);
      wait.hidden = true;
      frozen.forEach(function (el) { el.inert = false; });
      frozen = [];
    }

    // Shown at once, on the tap itself: Safari on an iPhone stops drawing
    // the page as soon as the next one is on its way, so anything left for
    // later never appears. These listeners are the last a tap reaches (the
    // document's own, added after the rest), so a "Delete...?" answered No or
    // a second press ignored has already said so.
    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (!e.defaultPrevented && (!form.target || form.target === "_self")) show();
    });
    document.addEventListener("click", function (e) {
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      var link = e.target.closest && e.target.closest("a[href]");
      if (!link || link.hasAttribute("download") || (link.target && link.target !== "_self")) return;
      var url = new URL(link.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      if (url.hash && url.pathname === window.location.pathname && url.search === window.location.search) return;
      if (e.defaultPrevented) return;
      try {
        if (link.matches('[aria-label^="Previous"], [aria-label^="The week before"], [rel="prev"]')
            || (link.closest(".ds-days")
                && link.compareDocumentPosition(document.querySelector(".ds-day.is-selected") || link) & Node.DOCUMENT_POSITION_FOLLOWING)) {
          sessionStorage.setItem("stm-slide", "back");
        }
      } catch (err) { /* storage blocked - slides forward */ }
      show();
    });
    // Back to this page from the next one: it's usable again.
    window.addEventListener("pageshow", function (e) { if (e.persisted) hide(); });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initTheme();
    initMenuToggle();
    initFlashes();
    initForms();
    initPageWait();
    initTimerClock();
    initTimerBar();
    initPalette();
    initTimeNudge();
    initLineMenu();
    initCalendarGrid();
    initWeekCopy();
    initBatch();
    initHeatmap();
    initArrange();
    initReminders();
    initNotifySetting();
    initIdle();
    initPickers();
    initBoards();
    initAutosave();
    initSlideOrder();
    initReportBars();
    initDayJump();
    initLiveTimer();
    initStatusPoll();
    initBreakRows();
    initAvatarPicker();
    initProfilePop();
    initMoreSheet();
    initPayAutosave();
    initDayType();
    initTimesheetCopy();
    initTimesheetFollow();
    initTimesheetScroll();
    initTimesheetPng();
  });
})();
