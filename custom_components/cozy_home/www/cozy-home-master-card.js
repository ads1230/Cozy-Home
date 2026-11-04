/*
 * Cozy Home thermostat card — the master (boiler) thermostat at a glance:
 * the current temperature large, whether it is heating, the target, and the
 * heating schedule's current block. −/+ nudge the target.
 *
 * Bundled with the Cozy Home integration and loaded automatically. Add a
 * card of type `custom:cozy-home-master-card` (no options required).
 *   title: Heating          # optional heading; omit for the thermostat's name only
 *   show_schedule: true     # the schedule line
 *   show_controls: false    # −/+ target buttons (off by default: the schedule drives the target)
 *   show_boost: true        # the Boost button (hold a temperature for a while, then resume)
 */
(() => {
  const DOMAIN = "cozy_home";
  const VERSION = "0.9.2"; // kept in step with manifest.json
  const CARD_TYPE = "cozy-home-master-card";

  const esc = (s) =>
    String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const num = (v) => (Number.isInteger(v) ? String(v) : String(Math.round(v * 10) / 10));
  const bad = (st) => !st || st.state === "unavailable" || st.state === "unknown";

  const fmtUntil = (iso, hass) => {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "?";
    const tz = hass && hass.config && hass.config.time_zone;
    try {
      return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: tz }).format(d);
    } catch (err) {
      return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(d);
    }
  };

  class CozyHomeMasterCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._config = {};
      this._busy = false;
      this._boostForm = null; // { key, temperature, minutes } while the chooser is open
      this.shadowRoot.addEventListener("click", (e) => this._onClick(e));
      this.shadowRoot.addEventListener("change", (e) => this._onChange(e));
    }

    static getStubConfig() {
      return {};
    }

    setConfig(config) {
      this._config = { show_schedule: true, show_controls: false, ...(config || {}) };
      if (this._hass) {
        this._collect();
        this._sig = this._signature();
      }
      this._render();
    }

    getCardSize() {
      return 3;
    }

    set hass(hass) {
      const first = !this._hass;
      this._hass = hass;
      this._collect();
      const sig = this._signature();
      if (first || sig !== this._sig) {
        this._sig = sig;
        this._render();
      }
    }

    get hass() {
      return this._hass;
    }

    _collect() {
      const hass = this._hass;
      this._hub = null;
      for (const [entityId, st] of Object.entries(hass.states)) {
        if (st.attributes.integration === DOMAIN && st.attributes.kind === "master") {
          this._hub = { entity_id: entityId, state: st };
          break;
        }
      }
      const attrs = this._hub ? this._hub.state.attributes : {};
      this._climateId = attrs.master_entity || null;
      this._climate = this._climateId ? hass.states[this._climateId] : null;
      this._enabled = attrs.enabled !== false;
    }

    _signature() {
      const c = this._climate;
      const h = this._hub && this._hub.state;
      return JSON.stringify([
        this._climateId,
        c && [c.state, c.attributes.current_temperature, c.attributes.temperature, c.attributes.hvac_action,
          c.attributes.friendly_name, c.attributes.preset_mode, c.attributes.min_temp, c.attributes.max_temp],
        h && [h.state, h.attributes.enabled, h.attributes.boost, h.attributes.block_pending],
        this._busy,
        this._boostForm,
      ]);
    }

    _unit() {
      const u = this._hass.config && this._hass.config.unit_system && this._hass.config.unit_system.temperature;
      return u || "°C";
    }

    async _setTarget(dir) {
      const c = this._climate;
      if (!c || this._busy) return;
      const step = Number(c.attributes.target_temp_step) || 0.5;
      let value = Number(c.attributes.temperature) + dir * step;
      if (c.attributes.min_temp != null) value = Math.max(Number(c.attributes.min_temp), value);
      if (c.attributes.max_temp != null) value = Math.min(Number(c.attributes.max_temp), value);
      value = Math.round(value * 10) / 10;
      this._busy = true;
      this._render();
      try {
        await this._hass.callService("climate", "set_temperature", { entity_id: this._climateId, temperature: value });
      } catch (err) {
        console.error("cozy-home-master-card", err);
      } finally {
        this._busy = false;
        this._render();
      }
    }

    _onClick(e) {
      const el = e.target.closest("[data-action]");
      if (!el) return;
      const { action } = el.dataset;
      if (action === "more-info" && this._climateId) {
        e.preventDefault();
        this.dispatchEvent(
          new CustomEvent("hass-more-info", { detail: { entityId: this._climateId }, bubbles: true, composed: true })
        );
      } else if (action === "down") {
        this._setTarget(-1);
      } else if (action === "up") {
        this._setTarget(1);
      } else if (action === "boost-open") {
        const c = this._climate;
        const base = c && c.attributes.temperature != null ? Number(c.attributes.temperature) : 20;
        this._boostForm = { key: "master", temperature: Math.round((base + 2) * 2) / 2, minutes: 60 };
        this._render();
      } else if (action === "boost-close") {
        this._boostForm = null;
        this._render();
      } else if (action === "boost-inc" || action === "boost-dec") {
        if (!this._boostForm) return;
        const dir = action === "boost-inc" ? 0.5 : -0.5;
        this._boostForm.temperature = Math.min(35, Math.max(5, this._boostForm.temperature + dir));
        this._render();
      } else if (action === "boost-start") {
        if (!this._boostForm || !this._hub) return;
        const f = this._boostForm;
        this._boostForm = null;
        this._busy = true;
        this._render();
        this._hass
          .callService(DOMAIN, "boost", { entity_id: this._hub.entity_id, temperature: f.temperature, minutes: f.minutes })
          .catch((err) => console.error("cozy-home-master-card", err))
          .finally(() => { this._busy = false; this._render(); });
      } else if (action === "boost-cancel") {
        e.preventDefault();
        if (!this._hub) return;
        this._hass.callService(DOMAIN, "cancel_boost", { entity_id: this._hub.entity_id }).catch((err) => console.error(err));
      }
    }

    _onChange(e) {
      const el = e.target;
      if (el.dataset.role === "boost-minutes" && this._boostForm) this._boostForm.minutes = Number(el.value);
    }

    _boostFormHTML(key) {
      const f = this._boostForm;
      if (!f || f.key !== key) return "";
      return `
        <div class="boostform">
          <span class="lbl2">Boost to</span>
          <button class="round sm" data-action="boost-dec" data-key="${esc(key)}">−</button>
          <span class="val">${esc(num(f.temperature))}°</span>
          <button class="round sm" data-action="boost-inc" data-key="${esc(key)}">+</button>
          <span class="lbl2">for</span>
          <select data-role="boost-minutes" data-key="${esc(key)}">
            ${[30, 60, 90, 120, 180, 240].map((m) => `<option value="${m}" ${f.minutes === m ? "selected" : ""}>${m < 60 ? m + " min" : m / 60 + " h" + (m % 60 ? " " + (m % 60) + " min" : "")}</option>`).join("")}
          </select>
          <button class="pill primary" data-action="boost-start" data-key="${esc(key)}">Start</button>
          <button class="pill" data-action="boost-close">Cancel</button>
        </div>`;
    }

    _boostStatusHTML(key, boost, entityId) {
      if (!boost) return "";
      const until = fmtUntil(boost.until, this._hass);
      return `<div class="boosting"><ha-icon icon="mdi:rocket-launch"></ha-icon>Boosting to ${esc(num(Number(boost.temperature)))}° until ${esc(until)}
        <a href="#" class="link" data-action="boost-cancel" data-key="${esc(key)}" data-entity="${esc(entityId)}">cancel</a></div>`;
    }


    _render() {
      if (!this._hass) return;
      const title = this._config.title ? `<div class="title">${esc(this._config.title)}</div>` : "";
      let body;
      if (!this._hub) {
        body = `<div class="empty">Cozy Home isn't set up yet — add the integration first. (If you have just updated Cozy Home, reload this page with Ctrl/Cmd-Shift-R.)</div>`;
      } else if (!this._climate || bad(this._climate)) {
        body = `<div class="empty">The master thermostat${this._climateId ? ` (${esc(this._climateId)})` : ""} is unavailable.</div>`;
      } else {
        body = this._mainHTML();
      }
      this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card>${title}${body}</ha-card>`;
    }

    _mainHTML() {
      const c = this._climate;
      const a = c.attributes;
      const unit = this._unit();
      const current = a.current_temperature;
      const target = a.temperature;
      const off = c.state === "off";
      const action = a.hvac_action;
      let statusIcon;
      let statusText;
      let statusClass;
      if (off) {
        statusIcon = "mdi:power-off";
        statusText = "Off";
        statusClass = "off";
      } else if (action === "heating" || action === "preheating") {
        statusIcon = "mdi:fire";
        statusText = target != null ? `Heating to ${esc(num(Number(target)))}°` : "Heating";
        statusClass = "heating";
      } else {
        statusIcon = "mdi:thermostat";
        statusText = target != null ? `Idle · set to ${esc(num(Number(target)))}°` : "Idle";
        statusClass = "idle";
      }
      const hub = this._hub.state;
      const schedText = bad(hub) ? "" : hub.state;
      const hasSchedule = schedText && !/^Not scheduled/.test(schedText);
      const preset = a.preset_mode && a.preset_mode !== "none" ? esc(a.preset_mode) : "";
      const scheduleLine =
        this._config.show_schedule && hasSchedule
          ? `<div class="sched"><ha-icon icon="mdi:calendar-clock"></ha-icon>Schedule: ${esc(schedText)}</div>`
          : "";
      const disabled = this._enabled ? "" : `<div class="warn"><ha-icon icon="mdi:alert-circle-outline"></ha-icon>Cozy Home is disabled — rooms aren't being controlled</div>`;
      const boost = hub.attributes.boost;
      const thermostatBoost = String(a.preset_mode || "").toLowerCase() === "boost" && !boost;
      const boostLine = boost
        ? this._boostStatusHTML("master", boost, this._hub.entity_id)
        : thermostatBoost
          ? `<div class="boosting"><ha-icon icon="mdi:rocket-launch"></ha-icon>Boost running (started on the thermostat)</div>`
          : "";
      const boostButton =
        this._config.show_boost !== false && !boost && !thermostatBoost && !this._boostForm // boosting from Off works too
          ? `<button class="boostbtn" data-action="boost-open" ${this._busy ? "disabled" : ""}><ha-icon icon="mdi:rocket-launch-outline"></ha-icon>Boost</button>`
          : "";
      const controls = this._config.show_controls
        ? `<div class="controls">
            <button class="round" data-action="down" ${this._busy || off ? "disabled" : ""} title="Lower the target">−</button>
            <span class="target">${target != null ? esc(num(Number(target))) + "°" : "–"}</span>
            <button class="round" data-action="up" ${this._busy || off ? "disabled" : ""} title="Raise the target">+</button>
          </div>`
        : "";
      return `
        <div class="top">
          <a href="#" class="name" data-action="more-info">
            <ha-icon icon="mdi:water-boiler" class="${statusClass}"></ha-icon>${esc(a.friendly_name || this._climateId)}
          </a>
          ${preset ? `<span class="mode">${preset}</span>` : ""}
        </div>
        <div class="main">
          <div class="reading">
            <span class="lbl">Current temp</span>
            <div><span class="big">${current != null ? esc(num(Number(current))) : "–"}</span><span class="unit">${esc(unit)}</span></div>
          </div>
          <div class="side">
            <div class="status ${statusClass}"><ha-icon icon="${statusIcon}"></ha-icon><span>${statusText}</span></div>
            ${controls}
          </div>
          <span class="spacer"></span>
          ${boostButton}
        </div>
        ${this._boostFormHTML("master")}
        ${boostLine}
        ${scheduleLine}
        ${disabled}`;
    }
  }

  const STYLE = `
    :host { --ch-follow: #E07A3A; --ch-off: #62A9B5; }
    ha-card { padding: 16px; }
    .title { font-size: 1.3em; font-weight: 500; margin-bottom: 8px; }
    .top { display: flex; align-items: center; gap: 8px; }
    .name { display: inline-flex; align-items: center; gap: 8px; color: var(--primary-text-color); text-decoration: none;
      font-weight: 500; --mdc-icon-size: 22px; }
    .name:hover { text-decoration: underline; }
    .name ha-icon { color: var(--secondary-text-color); }
    .name ha-icon.heating { color: var(--ch-follow); }
    .mode { margin-left: auto; color: var(--secondary-text-color); font-size: 0.85em; text-transform: capitalize; }
    .main { display: flex; align-items: center; gap: 24px; margin: 8px 0 4px; flex-wrap: wrap; }
    .reading { display: flex; flex-direction: column; gap: 2px; }
    .reading > div { display: flex; align-items: baseline; }
    .lbl { font-size: 0.72em; font-weight: 500; letter-spacing: 0.08em; text-transform: uppercase;
      color: var(--secondary-text-color); }
    .big { font-size: 3.4em; font-weight: 300; line-height: 1; letter-spacing: -0.02em; }
    .unit { font-size: 1.3em; color: var(--secondary-text-color); margin-left: 4px; }
    .side { display: flex; flex-direction: column; gap: 10px; flex: 1; min-width: 180px; }
    .status { display: inline-flex; align-items: center; gap: 8px; font-size: 1.05em; --mdc-icon-size: 22px; }
    .status.heating { color: var(--ch-follow); font-weight: 500; }
    .status.off { color: var(--ch-off); }
    .status.idle { color: var(--secondary-text-color); }
    .controls { display: inline-flex; align-items: center; gap: 8px; }
    .target { min-width: 3.5em; text-align: center; font-size: 1.1em; font-weight: 500; }
    button { font: inherit; cursor: pointer; }
    button:disabled { opacity: 0.4; cursor: default; }
    .round { width: 34px; height: 34px; border-radius: 50%; border: 1.5px solid var(--ch-follow); background: transparent;
      color: var(--ch-follow); font-size: 20px; line-height: 1; padding: 0; }
    .spacer { flex: 1; }

    .boostform { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-top: 8px; padding: 8px 10px;
      border-radius: 8px; background: var(--secondary-background-color, rgba(0,0,0,0.04)); }
    .boostform .lbl2 { color: var(--secondary-text-color); font-size: 0.9em; }
    .boostform select { font: inherit; color: var(--primary-text-color); background: var(--card-background-color, #fff);
      border: 1px solid var(--divider-color, #ddd); border-radius: 8px; padding: 4px 8px; }
    .boostform .val { min-width: 3em; text-align: center; font-weight: 500; }
    .boosting { display: flex; align-items: center; gap: 8px; margin-top: 8px; color: var(--ch-follow); font-size: 0.95em; --mdc-icon-size: 18px; }
    .boosting .link { color: var(--secondary-text-color); font-size: 0.9em; margin-left: 4px; }
    .boostbtn { height: 30px; padding: 0 12px; border-radius: 15px; border: 1.5px solid var(--ch-follow); background: transparent;
      color: var(--ch-follow); display: inline-flex; align-items: center; gap: 4px; --mdc-icon-size: 16px; font-size: 0.9em; }
    .round.sm { width: 28px; height: 28px; border-radius: 50%; border: 1.5px solid var(--ch-follow); background: transparent;
      color: var(--ch-follow); font-size: 16px; line-height: 1; padding: 0; }
    .pill { height: 30px; padding: 0 14px; border-radius: 15px; border: 1px solid var(--divider-color, #ddd);
      background: transparent; color: var(--primary-text-color); }
    .pill.primary { background: var(--ch-follow); border-color: var(--ch-follow); color: #fff; }
    .sched, .warn { display: flex; align-items: center; gap: 8px; margin-top: 10px; color: var(--secondary-text-color);
      font-size: 0.9em; --mdc-icon-size: 18px; }
    .warn { color: var(--warning-color, #b26a00); }
    .empty { color: var(--secondary-text-color); }
    @media (max-width: 480px) {
      .big { font-size: 2.8em; }
      .main { gap: 16px; }
    }
  `;

  if (!customElements.get(CARD_TYPE)) customElements.define(CARD_TYPE, CozyHomeMasterCard);
  window.cozyHome = window.cozyHome || {};
  window.cozyHome.cards = { ...(window.cozyHome.cards || {}), master: { cls: CozyHomeMasterCard, version: VERSION } };
  window.customCards = window.customCards || [];
  if (!window.customCards.some((c) => c.type === CARD_TYPE)) {
    window.customCards.push({
      type: CARD_TYPE,
      name: "Cozy Home Thermostat",
      description: "The master (boiler) thermostat: current temperature, heating state, target and the schedule's current block.",
      preview: true,
    });
  }
})();
