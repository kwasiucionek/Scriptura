/* Mapa miejsc biblijnych (Leaflet + OpenStreetMap). Dane: OpenBible.info Bible Geocoding Data, CC BY 4.0.
   Użycie: ScripturaPlaces.render(containerEl, places[, opts]) — `places` to lista z panelu „Miejsca”
   (places.service.PlaceOut). Najbardziej prawdopodobna lokalizacja: pełny znacznik; pozostali
   kandydaci: puste kółka z mniejszym kryciem. Regiony: okrąg o promieniu radius_m (gdy znany).
   Mapa inicjowana leniwie — po pokazaniu kontenera (details/toggle), bo Leaflet potrzebuje wymiarów. */
(function () {
  const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
  const ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> · miejsca: <a href="https://www.openbible.info/geo/">OpenBible.info</a> CC BY';
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const pct = (x) => Math.round((x || 0) * 100) + "%";
  const accent = () => getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#7a1f2b";

  function popup(p, loc, best) {
    const refs = (p.refs || []).map(esc).join(", ");
    return `<div class="pl-pop"><b>${esc(p.name)}</b>${p.name_en && p.name_en !== p.name ? ` <span class="muted">(${esc(p.name_en)})</span>` : ""}
      <div>${esc(loc.name)}${loc.type ? ` · ${esc(loc.type)}` : ""} · pewność ${pct(loc.confidence)}${best ? "" : " · inny kandydat"}</div>
      ${refs ? `<div class="muted">${refs}${p.verse_count > (p.refs || []).length ? ` · łącznie ${p.verse_count} wersetów` : ""}</div>` : ""}
      ${p.url ? `<div><a href="${esc(p.url)}" target="_blank" rel="noopener">OpenBible</a>${p.wikidata ? ` · <a href="https://www.wikidata.org/wiki/${esc(p.wikidata)}" target="_blank" rel="noopener">Wikidata</a>` : ""}</div>` : ""}</div>`;
  }

  function render(el, places, opts = {}) {
    if (!window.L || !el || el.dataset.ready) return null;
    el.dataset.ready = "1";
    const map = L.map(el, { scrollWheelZoom: false, attributionControl: true });
    L.tileLayer(TILES, { maxZoom: 15, attribution: ATTR }).addTo(map);
    const color = accent();
    const bounds = [];
    places.forEach((p, i) => {
      (p.locations || []).forEach((loc, j) => {
        const best = j === 0;
        const ll = [loc.lat, loc.lon];
        let m;
        if (loc.geometry === "region" && loc.radius_m) {
          m = L.circle(ll, { radius: loc.radius_m, color, weight: 1, fillOpacity: best ? 0.12 : 0.05, opacity: best ? 0.8 : 0.4 });
        } else {
          m = L.circleMarker(ll, { radius: best ? 7 : 5, color, weight: best ? 2 : 1.5, fillColor: color, fillOpacity: best ? 0.85 : 0.08, opacity: best ? 1 : 0.55 });
        }
        m.bindPopup(popup(p, loc, best)).addTo(map);
        if (best) {
          m.bindTooltip(p.name, { permanent: places.length <= 6, direction: "right", className: "pl-label", offset: [6, 0] });
          bounds.push(ll);
        } else if (opts.fitAll) bounds.push(ll);
      });
    });
    if (bounds.length) map.fitBounds(bounds, { padding: [24, 24], maxZoom: bounds.length === 1 ? 9 : 11 });
    else map.setView([31.8, 35.2], 6);
    el._leaflet = map;
    return map;
  }

  // Lista pod mapą: nazwa, lokalizacja, pewność, sigla; klik -> wycentruj
  function list(places) {
    return places.map((p) => {
      const loc = (p.locations || [])[0];
      if (!loc) return "";
      const more = (p.locations || []).length - 1;
      return `<div class="place" data-lat="${loc.lat}" data-lon="${loc.lon}"><b>${esc(p.name)}</b>${p.name_en && p.name_en !== p.name ? ` <span class="muted">${esc(p.name_en)}</span>` : ""}
        <span class="badge cur">${pct(loc.confidence)}</span>${more > 0 ? ` <span class="muted">+${more} ${more === 1 ? "kandydat" : "kandydatów"}</span>` : ""}${p.via === "name" ? ' <span class="badge ok">z pytania</span>' : ""}
        <div class="s">${esc(loc.name)}${p.refs?.length ? " · " + p.refs.map(esc).join(", ") : ""}</div></div>`;
    }).join("");
  }

  function wire(root) {
    const mapEl = root.querySelector(".pl-map");
    if (!mapEl) return;
    const places = JSON.parse(root.querySelector(".pl-data").textContent || "[]");
    const start = () => { const m = render(mapEl, places); if (!m && mapEl._leaflet) setTimeout(() => mapEl._leaflet.invalidateSize(), 50); };
    const details = root.closest("details");
    if (details && !details.open) details.addEventListener("toggle", () => details.open && start(), { once: true });
    else start();
    root.querySelectorAll(".place").forEach((d) => d.addEventListener("click", () => {
      const m = mapEl._leaflet; if (!m) return;
      m.setView([+d.dataset.lat, +d.dataset.lon], Math.max(m.getZoom(), 10));
    }));
  }

  // Znaczniki HTML: <div class="pl-root"><script type="application/json" class="pl-data">…</script><div class="pl-map"></div><div class="pl-list"></div></div>
  function block(places, id) {
    return `<div class="pl-root" id="${esc(id || "")}"><script type="application/json" class="pl-data">${JSON.stringify(places).replace(/</g, "\\u003c")}</script>
      <div class="pl-map" role="img" aria-label="Mapa miejsc biblijnych"></div><div class="pl-list">${list(places)}</div>
      <div class="muted small pl-attr">Lokalizacje wg OpenBible.info Bible Geocoding Data (CC BY 4.0); pewność = udział głosów źródeł za tą identyfikacją. Punkty, nie granice — przebieg tras i zasięg królestw to interpretacje, których tu nie ma.</div></div>`;
  }

  window.ScripturaPlaces = { render, list, block, wire };
})();
