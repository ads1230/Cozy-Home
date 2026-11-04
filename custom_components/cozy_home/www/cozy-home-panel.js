/*
 * Cozy Home sidebar panel: the rooms overview and the schedule editor on one
 * page, reachable from "Cozy Home" in the left menu. Home Assistant loads
 * this module on demand when the panel is opened, so it doesn't depend on the
 * dashboard page having picked up the card scripts.
 */
const version = new URL(import.meta.url).searchParams.get("v") || "";
const q = version ? `?v=${encodeURIComponent(version)}` : "";
const cardsReady = Promise.all([
  import(`./cozy-home-rooms-card.js${q}`),
  import(`./cozy-home-schedule-card.js${q}`),
]);

/**
 * Create a card element from the class the module we just loaded registered,
 * under a version-stamped tag name. A dashboard page may still hold an older
 * copy of the card under the plain tag (custom elements can't be redefined),
 * and this sidesteps it.
 */
function freshElement(plainTag, key) {
  const entry = window.cozyHome && window.cozyHome.cards && window.cozyHome.cards[key];
  if (entry && entry.cls && entry.version) {
    const tag = `${plainTag}-v${String(entry.version).replace(/[^0-9a-z]/gi, "-").toLowerCase()}`;
    if (!customElements.get(tag)) customElements.define(tag, class extends entry.cls {});
    return document.createElement(tag);
  }
  return document.createElement(plainTag);
}

class CozyHomePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._narrow = false;
    this._built = false;
  }

  connectedCallback() {
    if (!this._built) this._build();
  }

  set hass(hass) {
    this._hass = hass;
    this._push();
  }

  get hass() {
    return this._hass;
  }

  set narrow(narrow) {
    this._narrow = !!narrow;
    this.shadowRoot.host.classList.toggle("narrow", this._narrow);
    if (this._bar) this._bar.classList.toggle("narrow", this._narrow);
  }

  set panel(panel) {
    this._panel = panel;
  }

  _build() {
    this._built = true;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; min-height: 100vh; background: var(--primary-background-color, #fafafa);
          color: var(--primary-text-color); font-family: var(--primary-font-family, Roboto, sans-serif); }
        .bar { position: sticky; top: 0; z-index: 2; display: flex; align-items: center; gap: 8px; height: 56px;
          padding: 0 12px; background: var(--app-header-background-color, var(--primary-color));
          color: var(--app-header-text-color, #fff); box-shadow: 0 1px 4px rgba(0,0,0,0.2); }
        .bar .menu { display: none; width: 40px; height: 40px; border: none; background: transparent; color: inherit;
          border-radius: 50%; cursor: pointer; --mdc-icon-size: 24px; }
        .bar.narrow .menu { display: inline-flex; align-items: center; justify-content: center; }
        .bar .title { font-size: 20px; font-weight: 400; }
        .content { max-width: 960px; margin: 0 auto; padding: 16px; display: flex; flex-direction: column; gap: 16px;
          box-sizing: border-box; }
        .note { padding: 16px; color: var(--secondary-text-color); }
        .error { color: var(--error-color, #c62828); white-space: pre-wrap; }
      </style>
      <div class="bar${this._narrow ? " narrow" : ""}">
        <button class="menu" aria-label="Menu"><ha-icon icon="mdi:menu"></ha-icon></button>
        <div class="title">Cozy Home</div>
      </div>
      <div class="content"><div class="note">Loading…</div></div>`;
    this._bar = this.shadowRoot.querySelector(".bar");
    this._bar.querySelector(".menu").addEventListener("click", () => {
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }));
    });
    const content = this.shadowRoot.querySelector(".content");
    cardsReady.then(
      () => {
        content.innerHTML = "";
        this._rooms = freshElement("cozy-home-rooms-card", "rooms");
        this._rooms.setConfig({ title: "Rooms" });
        this._schedule = freshElement("cozy-home-schedule-card", "schedule");
        this._schedule.setConfig({ title: "Schedule" });
        content.append(this._rooms, this._schedule);
        this._push();
      },
      (err) => {
        content.innerHTML = `<div class="note error">Cozy Home could not load its cards:\n${String(err && err.message ? err.message : err)}</div>`;
      }
    );
  }

  _push() {
    if (!this._hass || !this._rooms) return;
    this._rooms.hass = this._hass;
    this._schedule.hass = this._hass;
  }
}

if (!customElements.get("cozy-home-panel")) customElements.define("cozy-home-panel", CozyHomePanel);
