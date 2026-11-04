/*
 * Cozy Home schedule card — the heating schedule, Hive-style.
 *
 * One weekly timeline of temperatures (and ❄ off) for the master
 * thermostat; rooms follow it with their temperature offsets. Bundled with
 * the Cozy Home integration and loaded automatically. Add a card of type
 * `custom:cozy-home-schedule-card` (no options required).
 *
 * Advanced: `entity: sensor.<room>_schedule` edits a per-room schedule
 * instead (those sensors are disabled by default).
 *
 * Reads the schedule from the hub's `sensor.<hub>_schedule` entity and
 * saves it with the `cozy_home.set_schedule` action.
 */
(() => {
  const DOMAIN = "cozy_home";
  const VERSION = "0.8.6"; // kept in step with manifest.json
  const CARD_TYPE = "cozy-home-schedule-card";
  const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const DAY_SHORT = ["M", "T", "W", "T", "F", "S", "S"];
  const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const STEP = 15; // minutes
  const TEMP_MIN = 5;
  const TEMP_MAX = 30;
  const OFFSET_MIN = -10;
  const OFFSET_MAX = 10;
  const MODE_LABEL = { follow: "Master", fixed: "Temperature", off: "Off" };
  const DEFAULT_MASTER = {
    all: [
      { time: "00:00", mode: "off" },
      { time: "06:30", mode: "fixed", temperature: 20 },
      { time: "08:30", mode: "fixed", temperature: 16 },
      { time: "17:00", mode: "fixed", temperature: 20 },
      { time: "22:00", mode: "off" },
    ],
  };

  const toMin = (t) => {
    const [h, m] = String(t).split(":").map(Number);
    return h * 60 + m;
  };
  const toTime = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  const snap = (m) => Math.round(m / STEP) * STEP;
  const clone = (o) => (o == null ? o : JSON.parse(JSON.stringify(o)));
  const num = (v) => (Number.isInteger(v) ? String(v) : String(Math.round(v * 10) / 10));
  const fmtTemp = (v) => `${num(v)}°`;
  const fmtOffset = (v) => (v > 0 ? `+${num(v)}°` : v < 0 ? `−${num(-v)}°` : "±0°");
  const esc = (s) =>
    String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  function sortBlocks(blocks) {
    const byTime = new Map();
    for (const b of blocks) byTime.set(b.time, b);
    return [...byTime.values()].sort((a, b) => toMin(a.time) - toMin(b.time));
  }

  /** Expand shortcut keys (all / weekdays / weekend) into days. */
  function expand(raw) {
    const out = {};
    const put = (days, blocks) => days.forEach((d) => (out[d] = clone(blocks)));
    if (raw.all) put(DAYS, raw.all);
    if (raw.weekdays) put(DAYS.slice(0, 5), raw.weekdays);
    if (raw.weekend) put(DAYS.slice(5), raw.weekend);
    for (const d of DAYS) if (raw[d]) out[d] = clone(raw[d]);
    return out;
  }

  /**
   * Turn a stored schedule into the editor's model: every day has a block at
   * 00:00 (carrying over the previous day's last block, like a thermostat).
   * Returns null for an empty master schedule (= "HA doesn't drive it").
   */
  function normalize(raw, kind) {
    const sched = expand(raw || {});
    const hasAny = DAYS.some((d) => (sched[d] || []).length);
    if (!hasAny) {
      if (kind === "master") return null;
      const out = {};
      DAYS.forEach((d) => (out[d] = [{ time: "00:00", mode: "follow" }]));
      return out;
    }
    let carry = null;
    for (let i = 6; i >= 0 && !carry; i--) {
      const b = sched[DAYS[i]];
      if (b && b.length) carry = clone(sortBlocks(b).at(-1));
    }
    const out = {};
    for (const d of DAYS) {
      const blocks = sortBlocks(clone(sched[d] || []));
      if (!blocks.length || blocks[0].time !== "00:00") blocks.unshift({ ...clone(carry), time: "00:00" });
      out[d] = blocks;
      carry = blocks.at(-1);
    }
    return out;
  }

  class CozyHomeScheduleCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._config = {};
      this._targets = [];
      this._selected = null; // entity_id of the schedule sensor
      this._saved = null;
      this._draft = null;
      this._sel = null; // { day, idx }
      this._copyOpen = false;
      this._copyDays = new Set();
      this._busy = false;
      this._message = null;
      this._drag = null;
      this._rendered = false;
      this.shadowRoot.addEventListener("click", (e) => this._onClick(e));
      this.shadowRoot.addEventListener("change", (e) => this._onChange(e));
      this.shadowRoot.addEventListener("pointerdown", (e) => this._onPointerDown(e));
    }

    static getStubConfig() {
      return {};
    }

    setConfig(config) {
      this._config = config || {};
      if (this._config.entity) this._selected = this._config.entity;
      if (this._hass) {
        this._refreshTargets();
        if (!this._dirty()) this._loadFromHass();
        this._sig = this._signature();
      }
      this._render();
    }

    getCardSize() {
      return 9;
    }

    set hass(hass) {
      // Called on every state change in Home Assistant. Only touch the DOM
      // when something this card shows actually changed, otherwise taps and
      // typing get interrupted by needless re-renders.
      const first = !this._hass;
      this._hass = hass;
      this._refreshTargets();
      if (!this._dirty()) this._loadFromHass();
      const sig = this._signature();
      if (first || sig !== this._sig) {
        this._sig = sig;
        if (!this._drag) this._render();
      } else {
        this._renderNow();
      }
    }

    _signature() {
      const t = this._target();
      const states = this._hass.states;
      const pick = (id) => {
        const st = id && states[id];
        return st ? [st.state, st.attributes.reason] : null;
      };
      const sched = t && states[t.entity_id];
      return JSON.stringify([
        this._targets.map((x) => [x.entity_id, x.name, x.kind]),
        this._selected,
        sched && sched.attributes.schedule,
        sched && sched.attributes.active_time,
        t && pick(t.modeEntity),
        t && pick(t.targetEntity),
        (() => {
          const c = sched && sched.attributes.master_entity && states[sched.attributes.master_entity];
          return c ? [c.state, c.attributes.hvac_modes] : null;
        })(),
        this._hass.config && this._hass.config.time_zone,
      ]);
    }

    get hass() {
      return this._hass;
    }

    connectedCallback() {
      this._nowTimer = setInterval(() => this._renderNow(), 30000);
    }

    disconnectedCallback() {
      clearInterval(this._nowTimer);
    }

    // ------------------------------------------------------------- data

    _refreshTargets() {
      const hass = this._hass;
      const entities = hass.entities || {};
      const devices = hass.devices || {};
      const targets = [];
      for (const [entityId, state] of Object.entries(hass.states)) {
        if (!entityId.startsWith("sensor.") || state.attributes.integration !== DOMAIN || !state.attributes.kind) continue;
        const reg = entities[entityId] || {};
        const device = reg.device_id ? devices[reg.device_id] : null;
        const name =
          (device && (device.name_by_user || device.name)) ||
          String(state.attributes.friendly_name || entityId).replace(/\s*Schedule$/i, "");
        // Sibling entities on the same device, for the status line.
        let targetEntity = null;
        let modeEntity = null;
        if (reg.device_id) {
          for (const [eid, e] of Object.entries(entities)) {
            if (e.device_id !== reg.device_id || e.platform !== DOMAIN) continue;
            if (e.translation_key === "target") targetEntity = eid;
            if (e.translation_key === "mode") modeEntity = eid;
          }
        }
        targets.push({
          entity_id: entityId,
          name,
          kind: state.attributes.kind,
          modes: state.attributes.modes || ["follow", "fixed", "off"],
          targetEntity,
          modeEntity,
        });
      }
      targets.sort((a, b) => (a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === "master" ? -1 : 1));
      this._targets = targets;
      if (!this._selected || !targets.some((t) => t.entity_id === this._selected)) {
        const master = targets.find((t) => t.kind === "master");
        this._selected = master ? master.entity_id : targets.length ? targets[0].entity_id : null;
        this._saved = this._draft = null;
        this._sel = null;
      }
    }

    _target() {
      return this._targets.find((t) => t.entity_id === this._selected) || null;
    }

    _loadFromHass() {
      const t = this._target();
      if (!t) {
        this._saved = this._draft = null;
        return;
      }
      const state = this._hass.states[t.entity_id];
      const raw = (state && state.attributes.schedule) || {};
      const normalized = normalize(raw, t.kind);
      const json = JSON.stringify(normalized);
      if (json !== JSON.stringify(this._saved)) {
        this._saved = normalized;
        this._draft = clone(normalized);
        if (this._sel && (!this._draft || !this._draft[DAYS[this._sel.day]][this._sel.idx])) this._sel = null;
      }
    }

    _dirty() {
      return JSON.stringify(this._draft) !== JSON.stringify(this._saved);
    }

    _selectedBlock() {
      if (!this._sel || !this._draft) return null;
      return this._draft[DAYS[this._sel.day]][this._sel.idx] || null;
    }

    _blockRange(day, idx) {
      const blocks = this._draft[DAYS[day]];
      const start = toMin(blocks[idx].time);
      const end = idx + 1 < blocks.length ? toMin(blocks[idx + 1].time) : 1440;
      return [start, end];
    }

    _now() {
      const tz = this._hass && this._hass.config && this._hass.config.time_zone;
      const opts = { weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" };
      let parts;
      try {
        parts = new Intl.DateTimeFormat("en-GB", { ...opts, timeZone: tz }).formatToParts(new Date());
      } catch (err) {
        parts = new Intl.DateTimeFormat("en-GB", opts).formatToParts(new Date());
      }
      const get = (type) => (parts.find((p) => p.type === type) || {}).value;
      const day = Math.max(0, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].indexOf(get("weekday")));
      const minutes = Number(get("hour")) * 60 + Number(get("minute"));
      return { day, minutes };
    }

    // ---------------------------------------------------------- actions

    async _callService(service, data, okText) {
      const t = this._target();
      if (!t) return;
      this._busy = true;
      this._message = null;
      this._render();
      try {
        await this._hass.callService(DOMAIN, service, { entity_id: t.entity_id, ...data });
        this._message = { kind: "ok", text: okText };
      } catch (err) {
        this._message = { kind: "error", text: (err && err.message) || String(err) };
      } finally {
        this._busy = false;
      }
    }

    async _save() {
      if (!this._draft) return;
      const schedule = {};
      for (const d of DAYS) schedule[d] = this._draft[d].map((b) => ({ ...b }));
      await this._callService("set_schedule", { schedule }, "Saved");
      if (this._message && this._message.kind === "ok") this._saved = clone(this._draft);
      this._render();
    }

    async _clear() {
      const t = this._target();
      const what = t && t.kind === "master" ? "Stop Home Assistant driving the master thermostat?" : "Remove this room's schedule? It will simply follow the master.";
      if (!window.confirm(what)) return;
      await this._callService("clear_schedule", {}, "Schedule removed");
      this._saved = this._draft = normalize({}, t.kind);
      this._sel = null;
      this._render();
    }

    _onClick(e) {
      const el = e.target.closest("[data-action]");
      if (!el || this._busy) return;
      const { action } = el.dataset;
      const day = el.dataset.day != null ? Number(el.dataset.day) : null;
      const idx = el.dataset.idx != null ? Number(el.dataset.idx) : null;
      const blk = this._selectedBlock();
      switch (action) {
        case "select-block":
          this._sel = { day, idx };
          this._copyOpen = false;
          break;
        case "add": {
          if (!blk) return;
          const [start, end] = this._blockRange(this._sel.day, this._sel.idx);
          if (end - start < 2 * STEP) return;
          const blocks = this._draft[DAYS[this._sel.day]];
          const nb = { ...clone(blk), time: toTime(snap((start + end) / 2)) };
          blocks.splice(this._sel.idx + 1, 0, nb);
          this._sel = { day: this._sel.day, idx: this._sel.idx + 1 };
          break;
        }
        case "remove":
          if (!blk || this._sel.idx === 0) return;
          this._draft[DAYS[this._sel.day]].splice(this._sel.idx, 1);
          this._sel = { day: this._sel.day, idx: this._sel.idx - 1 };
          break;
        case "copy":
          if (!this._sel) return;
          this._copyOpen = !this._copyOpen;
          this._copyDays = new Set();
          break;
        case "copy-day":
          this._copyDays.has(day) ? this._copyDays.delete(day) : this._copyDays.add(day);
          break;
        case "copy-apply": {
          const src = this._draft[DAYS[this._sel.day]];
          for (const d of this._copyDays) this._draft[DAYS[d]] = clone(src);
          this._copyOpen = false;
          break;
        }
        case "copy-cancel":
          this._copyOpen = false;
          break;
        case "mode": {
          if (!blk) return;
          const mode = el.dataset.mode;
          blk.mode = mode;
          if (mode === "fixed") {
            if (blk.temperature == null) blk.temperature = 18;
            delete blk.offset;
          } else if (mode === "follow") {
            delete blk.temperature;
          } else {
            delete blk.temperature;
            delete blk.offset;
          }
          break;
        }
        case "inc":
        case "dec": {
          if (!blk) return;
          const dir = action === "inc" ? 0.5 : -0.5;
          if (blk.mode === "fixed") {
            blk.temperature = Math.min(TEMP_MAX, Math.max(TEMP_MIN, (blk.temperature ?? 18) + dir));
          } else if (blk.mode === "follow") {
            blk.offset = Math.min(OFFSET_MAX, Math.max(OFFSET_MIN, (blk.offset ?? 0) + dir));
          }
          break;
        }
        case "reset-offset":
          if (blk) delete blk.offset;
          break;
        case "undo":
          this._draft = clone(this._saved);
          this._sel = null;
          this._copyOpen = false;
          this._message = null;
          break;
        case "save":
          this._save();
          return;
        case "clear":
          this._clear();
          return;
        case "create-master": {
          const t = this._target();
          this._draft = t && t.kind === "master" ? normalize(DEFAULT_MASTER, "master") : normalize({}, "room");
          this._sel = null;
          break;
        }
        default:
          return;
      }
      this._render();
    }

    _onChange(e) {
      const el = e.target;
      if (el.dataset.role === "target") {
        this._selected = el.value;
        this._saved = this._draft = null;
        this._sel = null;
        this._copyOpen = false;
        this._message = null;
        this._loadFromHass();
        this._render();
      } else if (el.dataset.role === "time") {
        const blk = this._selectedBlock();
        if (!blk || this._sel.idx === 0 || !el.value) return this._render();
        const blocks = this._draft[DAYS[this._sel.day]];
        const prev = toMin(blocks[this._sel.idx - 1].time);
        const next = this._sel.idx + 1 < blocks.length ? toMin(blocks[this._sel.idx + 1].time) : 1440;
        const m = Math.max(prev + STEP, Math.min(next - STEP, snap(toMin(el.value))));
        blk.time = toTime(m);
        this._render();
      }
    }

    _onPointerDown(e) {
      const h = e.target.closest("[data-handle]");
      if (!h || this._busy) return;
      const track = h.closest(".track");
      this._drag = {
        day: Number(h.dataset.day),
        idx: Number(h.dataset.idx),
        rect: track.getBoundingClientRect(),
        startX: e.clientX,
        moved: false,
      };
      e.preventDefault();
      const move = (ev) => this._onPointerMove(ev);
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", up);
        const d = this._drag;
        this._drag = null;
        if (d) {
          this._sel = { day: d.day, idx: d.idx };
          this._copyOpen = false;
          this._render();
        }
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", up);
    }

    _onPointerMove(e) {
      const d = this._drag;
      if (!d) return;
      const blocks = this._draft[DAYS[d.day]];
      const prev = toMin(blocks[d.idx - 1].time);
      const next = d.idx + 1 < blocks.length ? toMin(blocks[d.idx + 1].time) : 1440;
      let m = snap(((e.clientX - d.rect.left) / d.rect.width) * 1440);
      m = Math.max(prev + STEP, Math.min(next - STEP, m));
      const t = toTime(m);
      if (t !== blocks[d.idx].time) {
        blocks[d.idx].time = t;
        this._sel = { day: d.day, idx: d.idx };
        const trackEl = this.shadowRoot.querySelector(`.row[data-day="${d.day}"] .track`);
        if (trackEl) trackEl.innerHTML = this._trackHTML(d.day);
        const editor = this.shadowRoot.querySelector(".editor");
        if (editor) editor.innerHTML = this._editorInnerHTML();
        const tb = this.shadowRoot.querySelector(".toolbar");
        if (tb) tb.innerHTML = this._toolbarInnerHTML();
      }
    }

    // --------------------------------------------------------- rendering

    _render() {
      if (!this._hass) return;
      const t = this._target();
      const dirty = this._dirty();
      const showPicker = this._config.show_rooms && this._targets.length > 1;
      const header = `
        <div class="head">
          <div class="title">${esc(this._config.title || "Heating schedule")}</div>
          ${this._modeBadgeHTML(t)}
          ${showPicker
            ? `<select data-role="target" ${this._busy ? "disabled" : ""}>
                ${this._targets
                  .map(
                    (x) =>
                      `<option value="${esc(x.entity_id)}" ${x.entity_id === this._selected ? "selected" : ""}>${esc(x.name)}${x.kind === "master" ? " (master)" : ""}</option>`
                  )
                  .join("")}
              </select>`
            : ""}
        </div>`;

      let body;
      if (!t) {
        body = `<div class="empty">Cozy Home isn't set up yet — add the integration first. (If you have just updated Cozy Home, reload this page with Ctrl/Cmd-Shift-R.)</div>`;
      } else if (!this._draft) {
        body =
          t.kind === "master"
            ? `
          <div class="empty">
            <p>No heating schedule is set in Home Assistant.</p>
            <button class="pill primary" data-action="create-master">Create schedule</button>
          </div>`
            : `
          <div class="empty">
            <p><b>${esc(t.name)}</b> has no schedule of its own; it follows the heating schedule with its offset.</p>
            <button class="pill primary" data-action="create-master">Create a room schedule</button>
          </div>`;
      } else {
        body = `
          <div class="status">${this._statusHTML(t)}</div>
          <div class="toolbar">${this._toolbarInnerHTML()}</div>
          <div class="grid">
            <div class="hours"><div class="corner"></div><div class="axis">${this._axisHTML()}</div></div>
            ${DAYS.map((_, i) => this._rowHTML(i)).join("")}
          </div>
          ${this._copyOpen ? this._copyHTML() : ""}
          <div class="editor">${this._editorInnerHTML()}</div>
          <div class="legend">
            ${t.kind === "master" ? "" : `<span><i class="sw follow"></i>heating schedule + offset</span>`}
            <span><i class="sw fixed"></i>temperature</span>
            <span><i class="sw off"></i>off (frost protection)</span>
            <a href="#" data-action="clear" class="clear">${t.kind === "master" ? "Remove schedule (back to the thermostat's own)" : "Remove room schedule"}</a>
          </div>`;
      }

      const msg = this._message
        ? `<div class="msg ${this._message.kind}">${esc(this._message.text)}</div>`
        : dirty
          ? `<div class="msg hint">Unsaved changes</div>`
          : "";

      this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card>${header}${body}${msg}</ha-card>`;
      this._rendered = true;
      this._renderNow();
    }

    /** The thermostat's own mode (Auto = its app schedule, Manual, Off), if it has one. */
    _modeBadgeHTML(t) {
      if (!t || t.kind !== "master") return "";
      const sched = this._hass.states[t.entity_id];
      const climate = sched && sched.attributes.master_entity && this._hass.states[sched.attributes.master_entity];
      if (!climate || /^(unknown|unavailable)$/.test(climate.state)) return "";
      const modes = climate.attributes.hvac_modes || [];
      if (!modes.includes("auto")) return ""; // no schedule/manual distinction on this thermostat
      const label = climate.state === "auto" ? "Auto" : climate.state === "off" ? "Off" : "Manual";
      return `<span class="badge ${esc(climate.state)}" title="The thermostat's own mode">${label}</span>`;
    }

    _statusHTML(t) {
      const states = this._hass.states;
      if (t.kind === "master") {
        const sched = states[t.entity_id];
        const climate = sched && sched.attributes.master_entity && states[sched.attributes.master_entity];
        const now = sched && !/^(unknown|unavailable)$/.test(sched.state) ? sched.state : "";
        const cur = climate && climate.attributes.current_temperature != null ? ` · thermostat ${esc(num(Number(climate.attributes.current_temperature)))}° → ${esc(num(Number(climate.attributes.temperature)))}°` : "";
        return now ? `Now: ${esc(now)}${cur}` : "";
      }
      const mode = t.modeEntity && states[t.modeEntity];
      const target = t.targetEntity && states[t.targetEntity];
      if (!mode) return "";
      const modeText = esc(mode.state.charAt(0).toUpperCase() + mode.state.slice(1));
      const tgt = target && target.state !== "unknown" && target.state !== "unavailable" ? ` · ${esc(target.state)}°` : "";
      const reason = mode.attributes.reason ? ` (${esc(String(mode.attributes.reason).replace(/_/g, " "))})` : "";
      return `Now: ${modeText}${reason}${tgt}`;
    }

    _toolbarInnerHTML() {
      const blk = this._selectedBlock();
      let canAdd = false;
      if (blk) {
        const [s, e] = this._blockRange(this._sel.day, this._sel.idx);
        canAdd = e - s >= 2 * STEP;
      }
      const canRemove = !!blk && this._sel.idx > 0;
      const dirty = this._dirty();
      const dis = (ok) => (ok && !this._busy ? "" : "disabled");
      return `
        <button class="round" data-action="add" title="Add a block after the selected one" ${dis(canAdd)}><ha-icon icon="mdi:plus"></ha-icon></button>
        <button class="round" data-action="copy" title="Copy this day to other days" ${dis(!!blk)}><ha-icon icon="mdi:content-copy"></ha-icon></button>
        <button class="round" data-action="remove" title="Remove the selected block" ${dis(canRemove)}><ha-icon icon="mdi:minus"></ha-icon></button>
        <span class="spacer"></span>
        <button class="pill" data-action="undo" ${dis(dirty)}>Undo All</button>
        <button class="pill primary" data-action="save" ${dis(dirty)}>${this._busy ? "Saving…" : "Save"}</button>`;
    }

    _axisHTML() {
      let out = "";
      for (let h = 0; h <= 24; h += 3) {
        out += `<span class="${h % 6 ? "h3" : ""}" style="left:${(h / 24) * 100}%">${String(h % 24).padStart(2, "0")}</span>`;
      }
      return out + `<span class="nowpill" hidden></span>`;
    }

    _rowHTML(day) {
      return `
        <div class="row" data-day="${day}">
          <div class="dlabel" title="${DAY_NAMES[day]}">${DAY_SHORT[day]}</div>
          <div class="track">${this._trackHTML(day)}</div>
        </div>`;
    }

    _trackHTML(day) {
      const blocks = this._draft[DAYS[day]];
      let out = "";
      blocks.forEach((b, idx) => {
        const [start, end] = this._blockRange(day, idx);
        const left = (start / 1440) * 100;
        const width = ((end - start) / 1440) * 100;
        const selected = this._sel && this._sel.day === day && this._sel.idx === idx;
        const warmth = b.mode === "fixed" ? (b.temperature >= 20 ? " warm" : b.temperature >= 17 ? " mild" : "") : "";
        const cls = `blk ${b.mode}${warmth} ${idx === 0 ? "first" : ""} ${idx === blocks.length - 1 ? "last" : ""} ${selected ? "selected" : ""}`;
        const title = `${DAY_NAMES[day]} ${b.time}–${toTime(end % 1440)} · ${this._describe(b)}`;
        out += `<div class="${cls}" style="left:${left}%;width:${width}%" data-action="select-block" data-day="${day}" data-idx="${idx}" title="${esc(title)}">
            ${idx > 0 ? `<div class="grip" data-handle data-day="${day}" data-idx="${idx}"></div>` : ""}
            <span class="lbl">${this._blockLabel(b, width)}</span>
          </div>`;
      });
      return out + `<div class="nowline" hidden></div>`;
    }

    _describe(b) {
      if (b.mode === "off") return "off";
      if (b.mode === "fixed") return `fixed ${fmtTemp(b.temperature)}`;
      return b.offset != null ? `master ${fmtOffset(b.offset)}` : "master + room offset";
    }

    _blockLabel(b, width) {
      const tiny = width < 3.2;
      const narrow = width < 8;
      if (b.mode === "off") return tiny ? "" : `<ha-icon icon="mdi:snowflake"></ha-icon>`;
      if (b.mode === "fixed") return tiny ? "" : esc(fmtTemp(b.temperature));
      if (b.offset != null) return tiny ? "" : narrow ? esc(fmtOffset(b.offset)) : `master ${esc(fmtOffset(b.offset))}`;
      return tiny ? "" : narrow ? `<ha-icon icon="mdi:home-thermometer-outline"></ha-icon>` : "master";
    }

    _copyHTML() {
      const src = this._sel.day;
      return `
        <div class="panel copy">
          <span>Copy <b>${DAY_NAMES[src]}</b> to:</span>
          ${DAYS.map((_, i) =>
            i === src
              ? ""
              : `<button class="chip ${this._copyDays.has(i) ? "on" : ""}" data-action="copy-day" data-day="${i}">${DAY_SHORT[i]}</button>`
          ).join("")}
          <span class="spacer"></span>
          <button class="pill" data-action="copy-cancel">Cancel</button>
          <button class="pill primary" data-action="copy-apply" ${this._copyDays.size ? "" : "disabled"}>Apply</button>
        </div>`;
    }

    _editorInnerHTML() {
      const t = this._target();
      const blk = this._selectedBlock();
      if (!blk) return `<div class="hintline">Tap a block to edit it, or drag its left edge to change when it starts.</div>`;
      const { day, idx } = this._sel;
      const [start, end] = this._blockRange(day, idx);
      const modes = t.modes || ["follow", "fixed", "off"];
      let value;
      if (blk.mode === "fixed") {
        value = `<button class="round sm" data-action="dec">−</button><span class="val">${esc(fmtTemp(blk.temperature))}</span><button class="round sm" data-action="inc">+</button>`;
      } else if (blk.mode === "follow") {
        value = `<button class="round sm" data-action="dec">−</button>
          <span class="val">${blk.offset != null ? esc(fmtOffset(blk.offset)) : "room offset"}</span>
          <button class="round sm" data-action="inc">+</button>
          ${blk.offset != null ? `<a href="#" class="link" data-action="reset-offset">use room offset</a>` : ""}`;
      } else {
        value = `<span class="muted">${t.kind === "master" ? "Heating off (the thermostat's frost protection still applies)" : "Thermostat off — or frost protection, per the room's Off action"}</span>`;
      }
      return `
        <div class="edhead"><b>${DAY_NAMES[day]}</b> · ${esc(blk.time)} – ${esc(toTime(end % 1440))}</div>
        <div class="edrow">
          <label>Starts
            <input type="time" data-role="time" step="${STEP * 60}" value="${esc(blk.time)}" ${idx === 0 ? "disabled title='The first block of the day always starts at midnight'" : ""}>
          </label>
          <div class="seg">
            ${modes.map((m) => `<button class="${blk.mode === m ? "on" : ""}" data-action="mode" data-mode="${m}">${MODE_LABEL[m]}</button>`).join("")}
          </div>
          <div class="value">${value}</div>
        </div>`;
    }

    _renderNow() {
      if (!this._rendered || !this._draft) return;
      const { day, minutes } = this._now();
      const pct = (minutes / 1440) * 100;
      const pill = this.shadowRoot.querySelector(".nowpill");
      if (pill) {
        pill.hidden = false;
        pill.style.left = `${pct}%`;
        pill.textContent = toTime(minutes);
      }
      this.shadowRoot.querySelectorAll(".row").forEach((row) => {
        const isToday = Number(row.dataset.day) === day;
        row.classList.toggle("today", isToday);
        const line = row.querySelector(".nowline");
        if (line) {
          line.hidden = !isToday;
          line.style.left = `${pct}%`;
        }
      });
    }
  }

  const STYLE = `
    :host { --ch-follow: #E07A3A; --ch-fixed: #D6C94B; --ch-off: #62A9B5; --ch-fixed-ink: #3b2a26; }
    ha-card { padding: 16px; }
    .head { display: flex; align-items: center; gap: 12px; margin-bottom: 4px; }
    .badge { margin-left: auto; font-size: 0.8em; font-weight: 500; letter-spacing: 0.04em; text-transform: uppercase;
      padding: 3px 10px; border-radius: 12px; border: 1.5px solid var(--divider-color, #ddd); color: var(--secondary-text-color); }
    .badge.auto { border-color: var(--ch-off); color: var(--ch-off); }
    .badge.heat { border-color: var(--ch-follow); color: var(--ch-follow); }
    .head select { margin-left: auto; }
    .head .badge + select { margin-left: 0; }
    .title { font-size: 1.3em; font-weight: 500; flex: 1; }
    select { font: inherit; color: var(--primary-text-color); background: var(--card-background-color, #fff);
      border: 1px solid var(--divider-color, #ddd); border-radius: 8px; padding: 6px 10px; max-width: 60%; }
    .status { color: var(--secondary-text-color); font-size: 0.9em; min-height: 1.2em; margin-bottom: 8px; }
    .toolbar { display: flex; align-items: center; gap: 8px; margin: 4px 0 12px; }
    .spacer { flex: 1; }
    button { font: inherit; cursor: pointer; }
    button:disabled { opacity: 0.35; cursor: default; }
    .round { width: 36px; height: 36px; border-radius: 50%; border: 1.5px solid var(--ch-follow); background: transparent;
      color: var(--ch-follow); display: inline-flex; align-items: center; justify-content: center; padding: 0; --mdc-icon-size: 20px; }
    .round.sm { width: 30px; height: 30px; font-size: 18px; line-height: 1; }
    .pill { height: 36px; padding: 0 16px; border-radius: 18px; border: 1px solid var(--divider-color, #ddd);
      background: transparent; color: var(--primary-text-color); }
    .pill.primary:not(:disabled) { background: var(--ch-follow); border-color: var(--ch-follow); color: #fff; }
    .grid { position: relative; }
    .hours { display: flex; height: 22px; margin-bottom: 4px; }
    .corner { width: 28px; flex: none; }
    .axis { position: relative; flex: 1; font-size: 0.8em; color: var(--secondary-text-color); }
    .axis > span { position: absolute; transform: translateX(-50%); top: 0; }
    .axis .nowpill { background: var(--card-background-color, #fff); border: 2px solid var(--secondary-text-color);
      border-radius: 12px; padding: 1px 8px; font-size: 0.95em; color: var(--primary-text-color); top: -4px; z-index: 2; }
    .row { display: flex; align-items: center; height: 44px; margin-bottom: 8px; }
    .row.today .dlabel { font-weight: 700; color: var(--ch-follow); }
    .dlabel { width: 28px; flex: none; font-size: 0.95em; color: var(--primary-text-color); }
    .track { position: relative; flex: 1; height: 100%; touch-action: pan-y; }
    .blk { position: absolute; top: 0; bottom: 0; border-radius: 22px; display: flex; align-items: center; justify-content: center;
      color: #fff; font-size: 0.9em; font-weight: 500; cursor: pointer; box-sizing: border-box; overflow: hidden;
      border: 2px solid transparent; --mdc-icon-size: 18px; user-select: none; -webkit-user-select: none; }
    .blk.first { border-top-left-radius: 4px; border-bottom-left-radius: 4px; }
    .blk.last { border-top-right-radius: 4px; border-bottom-right-radius: 4px; }
    .blk.follow { background: var(--ch-follow); }
    .blk.fixed { background: var(--ch-fixed); color: var(--ch-fixed-ink); }
    .blk.fixed.mild { background: #F2A541; }
    .blk.fixed.warm { background: var(--ch-follow); color: #fff; }
    .blk.off { background: var(--ch-off); }
    .blk.selected { box-shadow: 0 0 0 2px var(--card-background-color, #fff), 0 0 0 4px var(--ch-follow); }
    .blk .lbl { pointer-events: none; white-space: nowrap; }
    .grip { position: absolute; left: 0; top: 0; bottom: 0; width: 14px; cursor: ew-resize; touch-action: none; }
    .grip::after { content: ""; position: absolute; left: 5px; top: 30%; bottom: 30%; width: 3px; border-radius: 2px;
      background: rgba(255,255,255,0.55); }
    .blk.fixed .grip::after { background: rgba(0,0,0,0.25); }
    .blk.fixed.warm .grip::after { background: rgba(255,255,255,0.55); }
    .nowline { position: absolute; top: -4px; bottom: -4px; width: 2px; background: var(--secondary-text-color); opacity: 0.7;
      transform: translateX(-1px); pointer-events: none; }
    .panel, .editor { margin-top: 8px; padding: 10px 12px; border-radius: 12px; background: var(--secondary-background-color, rgba(0,0,0,0.04)); }
    .copy { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
    .chip { width: 32px; height: 32px; border-radius: 50%; border: 1px solid var(--divider-color, #ddd); background: transparent; color: var(--primary-text-color); }
    .chip.on { background: var(--ch-follow); border-color: var(--ch-follow); color: #fff; }
    .edhead { margin-bottom: 8px; }
    .edrow { display: flex; align-items: center; flex-wrap: wrap; gap: 14px; }
    .edrow label { display: flex; align-items: center; gap: 8px; color: var(--secondary-text-color); font-size: 0.9em; }
    input[type=time] { font: inherit; color: var(--primary-text-color); background: var(--card-background-color, #fff);
      border: 1px solid var(--divider-color, #ddd); border-radius: 8px; padding: 4px 8px; }
    .seg { display: inline-flex; border: 1px solid var(--divider-color, #ddd); border-radius: 18px; overflow: hidden; }
    .seg button { height: 32px; padding: 0 14px; border: none; background: transparent; color: var(--primary-text-color); }
    .seg button.on { background: var(--ch-follow); color: #fff; }
    .value { display: inline-flex; align-items: center; gap: 8px; }
    .val { min-width: 4.5em; text-align: center; font-weight: 500; }
    .muted { color: var(--secondary-text-color); font-size: 0.9em; }
    .link, .clear { color: var(--ch-follow); font-size: 0.9em; }
    .hintline { color: var(--secondary-text-color); font-size: 0.9em; }
    .legend { display: flex; align-items: center; flex-wrap: wrap; gap: 14px; margin-top: 10px; font-size: 0.85em; color: var(--secondary-text-color); }
    .legend .clear { margin-left: auto; }
    .sw { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 5px; vertical-align: -1px; }
    .sw.follow { background: var(--ch-follow); } .sw.fixed { background: var(--ch-fixed); } .sw.off { background: var(--ch-off); }
    .empty { padding: 12px 0; color: var(--secondary-text-color); }
    .empty p { margin: 0 0 12px; }
    .msg { margin-top: 10px; font-size: 0.9em; }
    .msg.ok { color: var(--success-color, #2e7d32); } .msg.error { color: var(--error-color, #c62828); } .msg.hint { color: var(--secondary-text-color); }
    @media (max-width: 480px) {
      ha-card { padding: 12px; }
      .axis .h3 { display: none; }
      .row { height: 38px; margin-bottom: 6px; }
      .blk { font-size: 0.8em; border-radius: 19px; }
      select { max-width: 50%; }
    }
  `;

  if (!customElements.get(CARD_TYPE)) customElements.define(CARD_TYPE, CozyHomeScheduleCard);
  // The sidebar panel picks the class up from here, so it always uses the
  // code it just loaded even if a dashboard page has an older copy cached.
  window.cozyHome = window.cozyHome || {};
  window.cozyHome.cards = { ...(window.cozyHome.cards || {}), schedule: { cls: CozyHomeScheduleCard, version: VERSION } };
  window.customCards = window.customCards || [];
  if (!window.customCards.some((c) => c.type === CARD_TYPE)) {
    window.customCards.push({
      type: CARD_TYPE,
      name: "Cozy Home Schedule",
      description: "Hive-style weekly heating schedule for the master thermostat; rooms follow it with their offsets.",
      preview: true,
    });
  }
})();
