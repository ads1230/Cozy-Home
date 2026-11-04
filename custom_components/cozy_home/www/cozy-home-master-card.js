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
 */
(() => {
  const DOMAIN = "cozy_home";
  const VERSION = "0.8.8"; // kept in step with manifest.json
  const CARD_TYPE = "cozy-home-master-card";

  const esc = (s) =>
    String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const num = (v) => (Number.isInteger(v) ? String(v) : String(Math.round(v * 10) / 10));
  const bad = (st) => !st || st.state === "unavailable" || st.state === "unknown";

  class CozyHomeMasterCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._config = {};
      this._busy = false;
      this.shadowRoot.addEventListener("click", (e) => this._onClick(e));
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
        h && [h.state, h.attributes.enabled],
        this._busy,
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
      }
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
        </div>
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
