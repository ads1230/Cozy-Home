/*
 * Cozy Home rooms card — an overview of the master thermostat and every room:
 * temperature, target, what the room is doing, its offset, and the radiators
 * it controls. Tap a radiator for its own dialog; tap a room name for its
 * device page.
 *
 * Bundled with the Cozy Home integration and loaded automatically. Add a card
 * of type `custom:cozy-home-rooms-card`. Options (all optional):
 *   title: Heating
 *   rooms: [sensor.bedroom_schedule, ...]   # only these rooms, in this order
 *   show_thermostats: true                   # list each room's radiators
 *   show_master: true
 */
(() => {
  const DOMAIN = "cozy_home";
  const CARD_TYPE = "cozy-home-rooms-card";
  const MODE_TEXT = {
    follow: "Following master",
    fixed: "Fixed by schedule",
    off: "Off",
    frost: "Frost protection",
    hold: "Holding",
    disabled: "Disabled",
    unavailable: "Unavailable",
  };
  const MODE_ICON = {
    follow: "mdi:home-thermometer-outline",
    fixed: "mdi:calendar-clock",
    off: "mdi:power-off",
    frost: "mdi:snowflake",
    hold: "mdi:pause-circle-outline",
    disabled: "mdi:link-variant-off",
    unavailable: "mdi:help-circle-outline",
  };
  const REASON_TEXT = {
    schedule: "schedule",
    below_threshold: "below cut-off",
    master_off: "master off",
    master_unavailable: "master unavailable",
    room_unavailable: "thermostat unavailable",
    room_disabled: "follow master is off",
    global_disabled: "Cozy Home disabled",
  };

  const esc = (s) =>
    String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const num = (v) => (Number.isInteger(v) ? String(v) : String(Math.round(v * 10) / 10));
  const temp = (v) => (v == null || v === "" || Number.isNaN(Number(v)) ? "–" : `${num(Number(v))}°`);
  const signed = (v) => (v > 0 ? `+${num(v)}°` : v < 0 ? `−${num(-v)}°` : "±0°");
  const bad = (st) => !st || st.state === "unavailable" || st.state === "unknown";

  class CozyHomeRoomsCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._config = {};
      this._busy = new Set();
      this.shadowRoot.addEventListener("click", (e) => this._onClick(e));
    }

    static getStubConfig() {
      return { show_thermostats: true };
    }

    setConfig(config) {
      this._config = { show_thermostats: true, show_master: true, ...(config || {}) };
      if (this._hass) {
        this._collect();
        this._sig = this._signature();
      }
      this._render();
    }

    getCardSize() {
      return 2 + 2 * (this._rooms ? this._rooms.length : 3);
    }

    set hass(hass) {
      // Called on every state change in Home Assistant; only re-render when
      // something shown here changed, so taps aren't swallowed by re-renders.
      const first = !this._hass;
      this._hass = hass;
      this._collect();
      const sig = this._signature();
      if (first || sig !== this._sig) {
        this._sig = sig;
        this._render();
      }
    }

    _signature() {
      const states = this._hass.states;
      const st = (id) => {
        const s = id && states[id];
        if (!s) return null;
        const a = s.attributes;
        return [s.state, a.reason, a.thermostats, a.applied, a.active_block, a.current_temperature,
          a.temperature, a.hvac_action, a.friendly_name, a.enabled, a.master_entity];
      };
      const parts = [];
      if (this._master) {
        parts.push(this._master.name, st(this._master.ents.schedule),
          st(this._master.sched.attributes.master_entity));
      }
      for (const r of this._rooms || []) {
        parts.push(r.deviceId, r.name);
        for (const key of ["target", "mode", "offset", "follow_master", "schedule"]) parts.push(st(r.ents[key]));
        const target = states[r.ents.target];
        for (const id of (target && target.attributes.thermostats) || []) parts.push(st(id));
      }
      return JSON.stringify(parts);
    }

    get hass() {
      return this._hass;
    }

    // ------------------------------------------------------------- data

    _collect() {
      const hass = this._hass;
      const entities = hass.entities || {};
      const devices = hass.devices || {};
      const byDevice = new Map();
      for (const [entityId, e] of Object.entries(entities)) {
        if (e.platform !== DOMAIN || !e.device_id) continue;
        if (!byDevice.has(e.device_id)) byDevice.set(e.device_id, {});
        byDevice.get(e.device_id)[e.translation_key] = entityId;
      }
      // Fallback without a registry: find schedule sensors by their marker attribute.
      if (!byDevice.size) {
        for (const [entityId, st] of Object.entries(hass.states)) {
          if (st.attributes.integration === DOMAIN && st.attributes.kind) byDevice.set(entityId, { schedule: entityId });
        }
      }
      const rooms = [];
      let master = null;
      for (const [deviceId, ents] of byDevice) {
        const sched = ents.schedule && hass.states[ents.schedule];
        if (!sched) continue;
        const device = devices[deviceId] || {};
        const name =
          device.name_by_user || device.name || String(sched.attributes.friendly_name || "").replace(/\s*Schedule$/i, "");
        if (sched.attributes.kind === "master") {
          master = { deviceId, name, ents, sched };
        } else {
          rooms.push({ deviceId, name, ents, sched });
        }
      }
      rooms.sort((a, b) => a.name.localeCompare(b.name));
      if (Array.isArray(this._config.rooms) && this._config.rooms.length) {
        const order = this._config.rooms;
        const picked = rooms.filter((r) => order.includes(r.ents.schedule));
        picked.sort((a, b) => order.indexOf(a.ents.schedule) - order.indexOf(b.ents.schedule));
        this._rooms = picked;
      } else {
        this._rooms = rooms;
      }
      this._master = master;
    }

    _state(entityId) {
      return entityId ? this._hass.states[entityId] : undefined;
    }

    _roomView(room) {
      const hass = this._hass;
      const target = this._state(room.ents.target);
      const mode = this._state(room.ents.mode);
      const offset = this._state(room.ents.offset);
      const follow = this._state(room.ents.follow_master);
      const attrs = (target && target.attributes) || {};
      const thermostats = (attrs.thermostats || []).map((id) => {
        const st = hass.states[id];
        return {
          id,
          name: (st && st.attributes.friendly_name) || id,
          current: st && st.attributes.current_temperature,
          setpoint: st && st.attributes.temperature,
          hvac: st ? st.state : "unavailable",
          action: st && st.attributes.hvac_action,
          applied: (attrs.applied || {})[id],
        };
      });
      const temps = thermostats.map((t) => Number(t.current)).filter((v) => !Number.isNaN(v));
      const current = temps.length ? temps.reduce((a, b) => a + b, 0) / temps.length : null;
      return {
        ...room,
        modeKey: mode && !bad(mode) ? mode.state : "unavailable",
        reason: mode && mode.attributes.reason,
        target: target && !bad(target) ? Number(target.state) : null,
        offset: offset && !bad(offset) ? Number(offset.state) : null,
        offsetStep: (offset && offset.attributes.step) || 0.5,
        offsetMin: offset && offset.attributes.min,
        offsetMax: offset && offset.attributes.max,
        following: follow ? follow.state === "on" : null,
        activeBlock: attrs.active_block,
        current,
        thermostats,
        heating: thermostats.some((t) => t.action === "heating"),
      };
    }

    // ---------------------------------------------------------- actions

    async _call(key, domain, service, data) {
      this._busy.add(key);
      this._render();
      try {
        await this._hass.callService(domain, service, data);
      } catch (err) {
        console.error("cozy-home-rooms-card", err);
      } finally {
        this._busy.delete(key);
        this._render();
      }
    }

    _onClick(e) {
      const el = e.target.closest("[data-action]");
      if (!el) return;
      const { action, entity, device } = el.dataset;
      if (action === "more-info") {
        e.preventDefault();
        this.dispatchEvent(
          new CustomEvent("hass-more-info", { detail: { entityId: entity }, bubbles: true, composed: true })
        );
      } else if (action === "device") {
        e.preventDefault();
        const path = `/config/devices/device/${device}`;
        window.history.pushState(null, "", path);
        window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
      } else if (action === "offset") {
        if (this._busy.has(entity)) return;
        const step = Number(el.dataset.step) || 0.5;
        const dir = Number(el.dataset.dir);
        const st = this._hass.states[entity];
        if (!st) return;
        let value = Number(st.state) + dir * step;
        const min = st.attributes.min, max = st.attributes.max;
        if (min != null) value = Math.max(min, value);
        if (max != null) value = Math.min(max, value);
        value = Math.round(value * 10) / 10;
        this._call(entity, "number", "set_value", { entity_id: entity, value });
      } else if (action === "follow") {
        if (this._busy.has(entity)) return;
        this._call(entity, "switch", "toggle", { entity_id: entity });
      }
    }

    // --------------------------------------------------------- rendering

    _render() {
      if (!this._hass) return;
      const title = esc(this._config.title || "Heating");
      let body = "";
      if (!this._rooms || (!this._rooms.length && !this._master)) {
        body = `<div class="empty">No Cozy Home rooms found. Add the integration and some rooms first.</div>`;
      } else {
        if (this._config.show_master && this._master) body += this._masterHTML(this._master);
        body += this._rooms.map((r) => this._roomHTML(this._roomView(r))).join("");
      }
      this.shadowRoot.innerHTML = `<style>${STYLE}</style>
        <ha-card>
          <div class="head"><div class="title">${title}</div></div>
          ${body}
        </ha-card>`;
    }

    _masterHTML(master) {
      const attrs = master.sched.attributes || {};
      const climate = attrs.master_entity && this._hass.states[attrs.master_entity];
      const enabled = attrs.enabled !== false;
      const name = (climate && climate.attributes.friendly_name) || master.name;
      let detail;
      if (!climate || bad(climate)) {
        detail = `<span class="muted">unavailable</span>`;
      } else {
        const action = climate.attributes.hvac_action;
        detail = `
          <span class="big">${esc(temp(climate.attributes.current_temperature))}</span>
          <span class="arrow">→</span>
          <span class="big">${esc(temp(climate.attributes.temperature))}</span>
          <span class="chip ${climate.state === "off" ? "off" : action === "heating" ? "heat" : ""}">
            <ha-icon icon="${climate.state === "off" ? "mdi:power-off" : action === "heating" ? "mdi:fire" : "mdi:thermostat"}"></ha-icon>
            ${esc(climate.state === "off" ? "off" : action === "heating" ? "heating" : climate.state)}
          </span>`;
      }
      return `
        <div class="master ${enabled ? "" : "disabled"}">
          <div class="rowhead">
            <ha-icon icon="mdi:water-boiler" class="roomicon"></ha-icon>
            <a href="#" class="name" data-action="more-info" data-entity="${esc(attrs.master_entity || "")}">${esc(name)}</a>
            <span class="sub">master${attrs.active_block ? ` · schedule: ${esc(attrs.active_block)}` : ""}${enabled ? "" : " · Cozy Home is disabled"}</span>
          </div>
          <div class="vals">${detail}</div>
        </div>`;
    }

    _roomHTML(r) {
      const modeText = MODE_TEXT[r.modeKey] || r.modeKey;
      const reason = r.reason && REASON_TEXT[r.reason] ? ` · ${REASON_TEXT[r.reason]}` : "";
      const block = r.activeBlock ? ` · ${esc(r.activeBlock)}` : "";
      const busyOffset = this._busy.has(r.ents.offset) ? "disabled" : "";
      const busyFollow = this._busy.has(r.ents.follow_master) ? "disabled" : "";
      const thermostats = this._config.show_thermostats
        ? `<div class="trvs">${r.thermostats
            .map(
              (t) => `
              <a href="#" class="trv" data-action="more-info" data-entity="${esc(t.id)}" title="${esc(t.id)}">
                <ha-icon icon="${t.hvac === "unavailable" ? "mdi:radiator-off" : t.action === "heating" ? "mdi:radiator" : "mdi:radiator-disabled"}" class="${t.action === "heating" ? "heat" : ""}"></ha-icon>
                <span class="trvname">${esc(t.name)}</span>
                <span class="trvvals">${esc(temp(t.current))} <span class="arrow">→</span> ${
                  t.hvac === "off" ? "off" : esc(temp(t.setpoint))
                }${
                  t.applied != null && t.applied !== "unavailable" && t.applied !== t.setpoint && t.hvac !== "off"
                    ? ` <span class="muted">(set ${esc(temp(t.applied))})</span>`
                    : ""
                }</span>
              </a>`
            )
            .join("")}</div>`
        : "";
      return `
        <div class="room mode-${esc(r.modeKey)} ${r.following === false ? "disabled" : ""}">
          <div class="rowhead">
            <ha-icon icon="${MODE_ICON[r.modeKey] || "mdi:home"}" class="roomicon ${r.heating ? "heat" : ""}"></ha-icon>
            <a href="#" class="name" data-action="device" data-device="${esc(r.deviceId)}">${esc(r.name)}</a>
            <span class="sub">${esc(modeText)}${reason}${block}</span>
          </div>
          <div class="vals">
            <span class="big">${esc(temp(r.current))}</span>
            <span class="arrow">→</span>
            <span class="big">${r.modeKey === "off" ? "off" : esc(temp(r.target))}</span>
            <span class="spacer"></span>
            ${
              r.ents.offset
                ? `<span class="offset" title="Offset from the master target">
                    <button class="round" data-action="offset" data-entity="${esc(r.ents.offset)}" data-dir="-1" data-step="${r.offsetStep}" ${busyOffset}>−</button>
                    <span class="offval">${r.offset == null ? "–" : esc(signed(r.offset))}</span>
                    <button class="round" data-action="offset" data-entity="${esc(r.ents.offset)}" data-dir="1" data-step="${r.offsetStep}" ${busyOffset}>+</button>
                  </span>`
                : ""
            }
            ${
              r.ents.follow_master
                ? `<button class="toggle ${r.following ? "on" : ""}" data-action="follow" data-entity="${esc(r.ents.follow_master)}" title="Follow master" ${busyFollow}>
                    <span class="knob"></span>
                  </button>`
                : ""
            }
          </div>
          ${thermostats}
        </div>`;
    }
  }

  const STYLE = `
    :host { --ch-follow: #E07A3A; --ch-off: #62A9B5; --ch-fixed: #D6C94B; }
    ha-card { padding: 12px 16px 8px; }
    .head { display: flex; align-items: center; margin-bottom: 6px; }
    .title { font-size: 1.3em; font-weight: 500; }
    .master, .room { padding: 10px 0; border-top: 1px solid var(--divider-color, #e0e0e0); }
    .master { border-top: none; }
    .rowhead { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
    .roomicon { --mdc-icon-size: 20px; color: var(--secondary-text-color); align-self: center; }
    .roomicon.heat, ha-icon.heat { color: var(--ch-follow); }
    .name { font-weight: 500; color: var(--primary-text-color); text-decoration: none; }
    .name:hover { text-decoration: underline; }
    .sub { color: var(--secondary-text-color); font-size: 0.85em; }
    .vals { display: flex; align-items: center; gap: 8px; margin-top: 4px; padding-left: 28px; }
    .big { font-size: 1.25em; font-weight: 500; }
    .arrow { color: var(--secondary-text-color); }
    .spacer { flex: 1; }
    .muted { color: var(--secondary-text-color); font-size: 0.85em; }
    .chip { display: inline-flex; align-items: center; gap: 4px; font-size: 0.8em; padding: 2px 8px; border-radius: 12px;
      background: var(--secondary-background-color, rgba(0,0,0,0.05)); --mdc-icon-size: 14px; margin-left: 4px; }
    .chip.heat { background: var(--ch-follow); color: #fff; }
    .chip.off { background: var(--ch-off); color: #fff; }
    .offset { display: inline-flex; align-items: center; gap: 4px; }
    .offval { min-width: 3.2em; text-align: center; font-size: 0.95em; }
    button { font: inherit; cursor: pointer; }
    button:disabled { opacity: 0.4; cursor: default; }
    .round { width: 28px; height: 28px; border-radius: 50%; border: 1.5px solid var(--ch-follow); background: transparent;
      color: var(--ch-follow); font-size: 16px; line-height: 1; padding: 0; }
    .toggle { position: relative; width: 40px; height: 22px; border-radius: 11px; border: none; padding: 0;
      background: var(--disabled-color, #bdbdbd); transition: background .15s; }
    .toggle.on { background: var(--ch-follow); }
    .knob { position: absolute; top: 3px; left: 3px; width: 16px; height: 16px; border-radius: 50%; background: #fff;
      transition: left .15s; }
    .toggle.on .knob { left: 21px; }
    .room.disabled .big, .room.disabled .sub, .master.disabled .big { opacity: 0.5; }
    .trvs { display: flex; flex-direction: column; gap: 2px; margin-top: 6px; padding-left: 28px; }
    .trv { display: flex; align-items: center; gap: 8px; color: var(--primary-text-color); text-decoration: none;
      font-size: 0.9em; padding: 3px 6px; margin-left: -6px; border-radius: 8px; --mdc-icon-size: 18px; }
    .trv:hover { background: var(--secondary-background-color, rgba(0,0,0,0.04)); }
    .trv ha-icon { color: var(--secondary-text-color); }
    .trvname { flex: 1; }
    .trvvals { white-space: nowrap; color: var(--secondary-text-color); }
    .empty { padding: 12px 0; color: var(--secondary-text-color); }
    @media (max-width: 480px) {
      .vals { padding-left: 0; }
      .trvs { padding-left: 0; }
    }
  `;

  if (!customElements.get(CARD_TYPE)) customElements.define(CARD_TYPE, CozyHomeRoomsCard);
  window.customCards = window.customCards || [];
  if (!window.customCards.some((c) => c.type === CARD_TYPE)) {
    window.customCards.push({
      type: CARD_TYPE,
      name: "Cozy Home Rooms",
      description: "Overview of the master thermostat and every Cozy Home room: temperature, target, mode, offset and the radiators it controls.",
      preview: true,
    });
  }
})();
