"use strict";
// Where friction matters: the demand map with route profiles, and the checks against KYTC's
// inventories. Data in docs/data, built by `kyrg site`.

(async function main() {
  const css = getComputedStyle(document.documentElement);
  const token = (name) => css.getPropertyValue(name).trim();
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  const fmt = new Intl.NumberFormat("en-US");
  const pct = (v, d = 1) => `${(v * 100).toFixed(d)}%`;
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
  const svgNS = "http://www.w3.org/2000/svg";
  const getJSON = (url) => fetch(url).then((r) => {
    if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
    return r.json();
  });

  const site = await getJSON("data/site.json");
  const RAMP = [0, 1, 2, 3, 4].map((i) => token(`--q${i}`));
  const LAYERS = {   // key: [label, class breaks, value format, legend title]
    mu: ["Friction demand", [0.10, 0.15, 0.20, 0.25], (v) => v.toFixed(2), "Most friction needed, share of weight"],
    ms: ["Stopping", [0.2, 0.3, 0.45, 0.6], (v) => v.toFixed(2), "Friction to stop within the sight available"],
    g: ["Grade", [2, 4, 6, 8], (v) => `${v}%`, "Steepest grade"],
    d: ["Curvature", [3.5, 8.5, 14, 28], (v) => `${v}°`, "Sharpest curve, degree of curve"],
  };
  const CLASS_DEG = { A: "under 3.5°", B: "3.5–5.4°", C: "5.5–8.4°", D: "8.5–13.9°", E: "14–27.9°", F: "28° and up" };

  // "098-KY-3418-000" -> "KY 3418"
  const routeLabel = (route) => {
    const [, prefix, number] = route.split("-");
    return `${prefix} ${Number(number.replace(/\D/g, ""))}${number.replace(/^\d+/, "")}`;
  };
  const stamp = (s) => `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;

  fillText();
  renderKpis();
  renderChecks();
  renderCrashes();
  renderContext();
  initMap();

  // ======================= text, headline numbers and the checks =======================
  function fillText() {
    document.getElementById("nMiles").textContent = fmt.format(Math.round(site.network.miles));
    document.getElementById("nBlocks").textContent = fmt.format(site.lidar.blocks);
    document.getElementById("fetched").textContent = site.network.fetched;
    document.getElementById("snapshot").textContent = stamp(site.lidar.snapshot);
  }

  function renderKpis() {
    const kpis = [
      [fmt.format(Math.round(site.network.miles)), "miles of US and KY routes in District 12, both carriageways"],
      [fmt.format(site.lidar.blocks), "blocks of 2-ft lidar, each read once from KyFromAbove"],
      [pct(site.curves.found), "of KYTC's recorded curves found in the road's own geometry"],
      [pct(site.grades.crest_sag_agrees, 0), "of KYTC's crests and sags read the right way by the lidar"],
    ];
    document.getElementById("kpis").innerHTML = kpis.map(([v, l]) =>
      `<div class="kpi"><div class="value">${v}</div><div class="label">${l}</div></div>`).join("");
  }

  function renderChecks() {
    const c = site.curves, g = site.grades, b = g.bridges;
    document.getElementById("curvesText").textContent =
      `Of KYTC's ${fmt.format(c.inventory_curves)} recorded curves in the district, ${pct(c.found)} overlap a curve ` +
      `found in the road's geometry, and ${pct(c.detected_on_a_recorded_curve, 0)} of the ${fmt.format(c.detected)} curves ` +
      `found sit on a recorded one. Where a found curve and a record describe the same arc (${fmt.format(c.well_matched)} ` +
      `pairs overlapping by half their union or more), the degree of curve agrees with a median ratio of ` +
      `${c.degree_ratio_p50.toFixed(2)} and a rank correlation of ${c.degree_rank_corr.toFixed(2)}, and left and right ` +
      `agree ${pct(c.direction_agrees, 0)} of the time. A few routes record left and right swapped throughout.`;
    document.getElementById("curvesTable").innerHTML =
      "<thead><tr><th>Class</th><th>Degree of curve</th><th>Recorded curves found</th><th>Curves found that are recorded</th></tr></thead><tbody>" +
      Object.keys(CLASS_DEG).map((k) => `<tr><td>${k}</td><td>${CLASS_DEG[k]}</td><td>${pct(c.found_by_class[k] ?? NaN, 0)}</td>` +
        `<td>${c.detected_on_a_recorded_curve_by_class[k] == null ? "–" : pct(c.detected_on_a_recorded_curve_by_class[k], 0)}</td></tr>`).join("") +
      "</tbody>";

    document.getElementById("gradesText").textContent =
      `KYTC's grade inventory splits each route into uphill and downhill tangents, crests and sags. The lidar reads ` +
      `${pct(g.crest_sag_agrees, 0)} of the ${fmt.format(g.vertical_curves)} crests and sags the right way. On the ` +
      `${fmt.format(g.clean.tangents)} tangents where the lidar profile is a clean straight line, it agrees on uphill or ` +
      `downhill ${pct(g.clean.direction_agrees, 0)} of the time and puts the grade within one class ` +
      `${pct(g.clean.class_within_one, 0)} of the time. The inventory reads steeper, though, by a median ` +
      `${Math.abs(g.clean.grade_bias_p50).toFixed(1)} percentage points, uphill and downhill alike, so the lidar usually ` +
      `lands one class lower. The lidar's lines fit the profile within a median ${site.grade_fit_rmse_ft.toFixed(2)} ft, ` +
      `so the gap isn't lidar noise; how the inventory's grades were measured isn't published.`;
    const row = (label, s) => `<tr><td>${label}</td><td>${fmt.format(s.tangents)}</td><td>${pct(s.direction_agrees, 0)}</td>` +
      `<td>${pct(s.class_exact, 0)}</td><td>${pct(s.class_within_one, 0)}</td><td>${s.grade_bias_p50.toFixed(2)}</td>` +
      `<td>${s.rank_corr.toFixed(2)}</td></tr>`;
    document.getElementById("gradesTable").innerHTML =
      "<thead><tr><th>Tangents</th><th>Records</th><th>Direction agrees</th><th>Class exact</th><th>Within one class</th>" +
      "<th>Median lidar − KYTC (pts)</th><th>Rank corr.</th></tr></thead><tbody>" +
      row("All", g.all) + row("Clean straight-line fits", g.clean) + "</tbody>";

    document.getElementById("bridgesText").textContent =
      `A bare-earth model has the bridges taken out, so under one the profile falls into the valley and climbs back. ` +
      `${pct(b.dips_on_a_bridge, 0)} of the ${fmt.format(b.dips)} dips found sit on a recorded bridge, and they find ` +
      `${pct(b.found_by_length["500+"], 0)} of recorded bridges 500 ft or longer. Short spans over small creeks barely ` +
      `dip, so most go unseen; the road profile is decked over every recorded bridge anyway.`;
    document.getElementById("bridgesTable").innerHTML =
      "<thead><tr><th>Recorded bridge length</th><th>Found as a dip</th></tr></thead><tbody>" +
      Object.entries(b.found_by_length).map(([k, v]) => `<tr><td>${k.replace("-", "–")}${k.includes("ft") ? "" : " ft"}</td>` +
        `<td>${pct(v, 0)}</td></tr>`).join("") + "</tbody>";
  }

  // crash reports: counts and rates only (the reports are KSP's)
  function renderCrashes() {
    const k = site.crashes;
    if (!k) { document.getElementById("crashSection").hidden = true; return; }
    const o = k.officer, cc = o.coded_curve, cr = o.coded_crest;
    document.getElementById("crashIntro").textContent =
      `Kentucky State Police reports for ${k.years[0]}–${k.years[1]} name the road and county of each crash and give ` +
      `its coordinates. Of the ${fmt.format(k.on_routes)} on the district's US and KY routes, parking lots left out, ` +
      `${pct(k.placed / k.on_routes, 0)} were placed within ${k.snap_ft} ft of the route they name. The reports are ` +
      `KSP's, all rights reserved, and stay off this page: what's here are counts and rates over the whole district.`;
    document.getElementById("officerText").textContent =
      `Officers record whether the road was curved or straight where a crash happened, and whether it was level, on ` +
      `a grade or at a hillcrest. Crashes they put on a curve are within ${k.near_ft} ft of a curve found in the lidar ` +
      `${pct(cc.curve_lidar.when_coded, 0)} of the time; crashes they put on a straight road, ` +
      `${pct(cc.curve_lidar.when_not, 0)}. KYTC's curve inventory, which reaches within ${k.near_ft} ft of ` +
      `${pct(o.network_share.curve_kytc, 0)} of the district's road, tells them apart less well. Hillcrests are ` +
      `coded on few reports and loosely, but those crashes are more often where a crest leaves less sight than it ` +
      `takes to stop.`;
    const share = (v) => (v == null ? "–" : pct(v, 0));
    document.getElementById("officerTable").innerHTML =
      `<thead><tr><th>The officer coded the road as</th><th>Crashes</th><th>Curve found in the lidar</th>` +
      `<th>KYTC-recorded curve</th><th>Crest leaves too little sight to stop</th></tr></thead><tbody>` +
      `<tr><td>Curved</td><td>${fmt.format(cc.coded)}</td><td>${share(cc.curve_lidar.when_coded)}</td>` +
      `<td>${share(cc.curve_kytc.when_coded)}</td><td>–</td></tr>` +
      `<tr><td>Straight</td><td>${fmt.format(cc.not_coded)}</td><td>${share(cc.curve_lidar.when_not)}</td>` +
      `<td>${share(cc.curve_kytc.when_not)}</td><td>–</td></tr>` +
      `<tr><td>At a hillcrest</td><td>${fmt.format(cr.coded)}</td><td>–</td><td>–</td><td>${share(cr.short_sight.when_coded)}</td></tr>` +
      `<tr><td>Level or on a grade</td><td>${fmt.format(cr.not_coded)}</td><td>–</td><td>–</td><td>${share(cr.short_sight.when_not)}</td></tr>` +
      `<tr><td class="k">All the district's road</td><td>–</td><td>${share(o.network_share.curve_lidar)}</td>` +
      `<td>${share(o.network_share.curve_kytc)}</td><td>${share(o.network_share.short_sight)}</td></tr></tbody>`;

    const D = k.deciles, lo = D[0], hi = D[D.length - 1], m = k.model;
    const top = D.slice(D.length / 2);
    const sum = (rows, key) => rows.reduce((a, r) => a + r[key], 0);
    const topWet = sum(top, "wet_departure") / (sum(top, "wet_departure") + sum(top, "dry_departure"));
    const rr = (x) => `${x.rate_ratio.toFixed(2)} (95% interval ${x.ci[0].toFixed(2)}–${x.ci[1].toFixed(2)})`;
    document.getElementById("ratesText").textContent =
      `Rank every 0.1-mile piece of road by its most demanding point and cut the list into tenths, about ` +
      `${fmt.format(Math.round(k.pieces.miles / 10))} miles each. Roadway departures, single-vehicle crashes off the ` +
      `road or into something beside it, rise from ${lo.wet_departure_rate.toFixed(0)} per 100 million vehicle-miles ` +
      `on wet pavement in the least demanding tenth to ${hi.wet_departure_rate.toFixed(0)} in the most, and from ` +
      `${lo.dry_departure_rate.toFixed(0)} to ${hi.dry_departure_rate.toFixed(0)} on dry. Animal crashes, which the ` +
      `road's geometry shouldn't explain, fall from ${lo.animal_rate.toFixed(0)} to ${hi.animal_rate.toFixed(0)}. ` +
      `Allowing for traffic, each 0.1 more of demand multiplies the wet departure rate by ${rr(m.wet_departure)}, ` +
      `the dry by ${rr(m.dry_departure)} and the animal crash rate by ${m.animal.rate_ratio.toFixed(2)}. Wet ` +
      `pavement's share of departures rises from ${pct(lo.wet_share, 0)} in the least demanding tenth to ` +
      `${pct(topWet, 0)} across the most demanding half. Traffic is each KYTC section's latest count, mostly from ` +
      `2020–2022, held for every year, so the rates are approximate.`;

    const xs = D.map((r) => r.decile);
    const series = [
      { name: "Departures, dry", label: "Departures, dry", color: token("--s2"), xs, ys: D.map((r) => r.dry_departure_rate) },
      { name: "Departures, wet", label: "Departures, wet", color: token("--s1"), xs, ys: D.map((r) => r.wet_departure_rate) },
      { name: "Animal crashes", label: "Animal crashes", color: token("--context"), dash: true, xs, ys: D.map((r) => r.animal_rate) },
    ];
    const yMax = Math.ceil(Math.max(...series.flatMap((s) => s.ys)) / 10) * 10;
    const card = cardIn(document.getElementById("ratesChart"), "Crashes per 100 million vehicle-miles",
      "By tenth of the district's road, ranked by friction demand, lowest to highest. Dashed: animal crashes, the placebo.");
    lineChart(card, { H: 230, aria: "Crash rates by tenth of the road ranked by friction demand.",
      x: { min: 1, max: 10, ticks: xs, label: "tenth of the road by demand" },
      y: { min: 0, max: yMax, ticks: niceTicks(0, yMax, 10) }, series,
      tip: (i) => `<b>Tenth ${D[i].decile}</b>: demand ${D[i].mu_lo.toFixed(2)}–${D[i].mu_hi.toFixed(2)}<br>` +
        series.map((s) => `<i style="background:${s.color}"></i>${s.name} ${s.ys[i].toFixed(1)}`).join("<br>") });

    document.getElementById("ratesTable").innerHTML =
      "<caption>Crashes, with the rate per 100 million vehicle-miles in parentheses.</caption>" +
      "<thead><tr><th>Tenth</th><th>Demand</th><th>Miles</th><th>100M veh-mi</th><th>Departures, wet</th>" +
      "<th>Departures, dry</th><th>Animal</th><th>Wet share of departures</th></tr></thead><tbody>" +
      D.map((r) => `<tr><td>${r.decile}</td><td>${r.mu_lo.toFixed(2)}–${r.mu_hi.toFixed(2)}</td><td>${fmt.format(Math.round(r.miles))}</td>` +
        `<td>${r.vmt_100m.toFixed(1)}</td><td>${fmt.format(r.wet_departure)} (${r.wet_departure_rate.toFixed(1)})</td>` +
        `<td>${fmt.format(r.dry_departure)} (${r.dry_departure_rate.toFixed(1)})</td>` +
        `<td>${fmt.format(r.animal)} (${r.animal_rate.toFixed(1)})</td><td>${pct(r.wet_share, 0)}</td></tr>`).join("") +
      "</tbody>";
  }

  // ERS's ruggedness and rurality codes by census tract, and the road summed up by them
  async function renderContext() {
    const c = site.context;
    if (!c) { document.getElementById("contextSection").hidden = true; return; }
    const R = c.by_rrs, U = c.by_ruca;
    const hi = R.find((r) => r.rrs_name === "Highly rugged"), lo = R[0];
    const rural = U.find((r) => r.ruca_group === "Rural"), metro = U[0];
    document.getElementById("contextIntro").textContent =
      `The USDA Economic Research Service rates every census tract by how rugged its terrain is, across the tract ` +
      `and along its roads, and codes how rural it is by where its residents commute. District 12's ` +
      `${c.tracts} tracts are all at least slightly rugged along their roads, and ${c.tracts_by_rrs["Highly rugged"]} ` +
      `of them are among the ${c.kentucky_highly_rugged} of Kentucky's ${fmt.format(c.kentucky_tracts)} tracts rated ` +
      `highly rugged; ${c.tracts_by_ruca.Rural} are wholly rural.`;
    document.getElementById("contextText").textContent =
      `Within the district, the classes barely separate the road's geometry: every class here is mountain road, ` +
      `with ${lo.curves_per_mile.toFixed(1)} to ${hi.curves_per_mile.toFixed(1)} curves a mile and a median demand ` +
      `near ${hi.mu_p50.toFixed(2)}. Crash rates separate them more. Roadway departures on wet pavement run ` +
      `${hi.wet_departure_rate.toFixed(0)} per 100 million vehicle-miles in highly rugged tracts against ` +
      `${lo.wet_departure_rate.toFixed(0)} in slightly rugged ones, and ${rural.wet_departure_rate.toFixed(0)} in ` +
      `rural tracts against ${metro.wet_departure_rate.toFixed(0)} in metropolitan ones, on road that asks about as ` +
      `much of a car. Whatever the ruggedness class picks up beyond the road's geometry, from roadside slopes to ` +
      `lane widths, isn't measured here.`;
    const row = (label, r) => `<tr><td>${label}</td><td>${fmt.format(Math.round(r.miles))} (${pct(r.share, 0)})</td>` +
      `<td>${r.curves_per_mile.toFixed(1)}</td><td>${r.grade_p90.toFixed(1)}%</td><td>${r.mu_p50.toFixed(2)}</td>` +
      `<td>${pct(r.top_tenth, 0)}</td><td>${r.wet_departure_rate?.toFixed(1) ?? "–"}</td>` +
      `<td>${r.dry_departure_rate?.toFixed(1) ?? "–"}</td><td>${r.animal_rate?.toFixed(1) ?? "–"}</td></tr>`;
    const head = (t) => `<tr><td class="group" colspan="9">${t}</td></tr>`;
    document.getElementById("contextTable").innerHTML =
      "<caption>Crash rates per 100 million vehicle-miles.</caption>" +
      "<thead><tr><th></th><th>Miles of road</th><th>Curves a mile</th><th>Grade, steepest tenth</th>" +
      "<th>Median demand</th><th>In the district's most demanding tenth</th><th>Departures, wet</th>" +
      "<th>Departures, dry</th><th>Animal crashes</th></tr></thead><tbody>" +
      head("Road ruggedness") + R.map((r) => row(r.rrs_name, r)).join("") +
      head("Rurality") + U.map((r) => row(r.ruca_group, r)).join("") + "</tbody>";

    const tracts = await getJSON("data/tracts.geojson");
    const host = document.getElementById("tractMaps");
    const rrs = R.map((r) => r.rrs_name), ruca = U.map((r) => r.ruca_group);
    tractMap(host, tracts, "rrs", rrs, "Road ruggedness", "ERS's Road Ruggedness Scale, 2020 tracts");
    tractMap(host, tracts, "ruca", ruca, "Rurality", "ERS's primary RUCA code, grouped");
  }

  // a small choropleth of the tracts, classes on the sequential ramp, low to high
  function tractMap(host, gj, key, classes, title, sub) {
    const card = cardIn(host, title, sub);
    const steps = classes.length === 3 ? [1, 2, 4] : [0, 1, 3, 4];   // spread the classes over the ramp
    const color = Object.fromEntries(classes.map((k, i) => [k, RAMP[steps[i]]]));
    card.insertAdjacentHTML("beforeend", `<div class="legend-inline">${classes.map((k) =>
      `<span><i class="band" style="background:${color[k]}"></i>${k}</span>`).join("")}</div>`);
    const rings = (g) => (g.type === "Polygon" ? [g.coordinates] : g.coordinates).flat();
    let lon0 = Infinity, lon1 = -Infinity, lat0 = Infinity, lat1 = -Infinity;
    for (const f of gj.features) for (const ring of rings(f.geometry)) for (const [x, y] of ring) {
      lon0 = Math.min(lon0, x); lon1 = Math.max(lon1, x); lat0 = Math.min(lat0, y); lat1 = Math.max(lat1, y);
    }
    const kx = Math.cos(((lat0 + lat1) / 2) * Math.PI / 180);
    const W = 400, s = (W - 8) / ((lon1 - lon0) * kx), H = Math.round((lat1 - lat0) * s + 8);
    const { svg, add } = svgIn(card, W, H, `Map of District 12's census tracts by ${title.toLowerCase()}.`);
    const tip = document.createElement("div");
    tip.className = "tip";
    tip.hidden = true;
    card.appendChild(tip);
    for (const f of gj.features) {
      const d = rings(f.geometry).map((ring) => "M" + ring.map(([x, y]) =>
        `${(4 + (x - lon0) * kx * s).toFixed(1)},${(4 + (lat1 - y) * s).toFixed(1)}`).join("L") + "Z").join("");
      const path = add("path", { d, fill: color[f.properties[key]] ?? "none", "fill-rule": "evenodd",
        stroke: token("--surface"), "stroke-width": 1 });
      path.addEventListener("pointermove", (evt) => {
        const box = card.getBoundingClientRect(), p = f.properties;
        tip.hidden = false;
        tip.innerHTML = `<b>${esc(p.county)} County</b>, tract ${esc(p.geoid.slice(5))}<br>${esc(p[key])}` +
          `<br>${fmt.format(p.pop)} residents`;
        const left = evt.clientX - box.left + 14;
        tip.style.left = `${left + tip.offsetWidth > box.width ? left - tip.offsetWidth - 28 : left}px`;
        tip.style.top = `${evt.clientY - box.top + 10}px`;
      });
      path.addEventListener("pointerleave", () => { tip.hidden = true; });
    }
  }

  // ======================= the map =======================
  function initMap() {
    const state = { layer: "mu", ready: false, route: null };
    const seg = document.getElementById("layerSeg");
    seg.innerHTML = Object.entries(LAYERS).map(([k, [label]]) =>
      `<button type="button" data-k="${k}" aria-pressed="${k === state.layer}">${label}</button>`).join("");
    seg.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (b) setLayer(b.dataset.k);
    });

    const colorBy = (k) => {
      const [, br] = LAYERS[k];
      return ["step", ["get", k], RAMP[0], br[0], RAMP[1], br[1], RAMP[2], br[2], RAMP[3], br[3], RAMP[4]];
    };
    // a piece without a value for the layer (no elevation there) is left off, not drawn in the fallback black
    const hasValue = (k) => ["==", ["typeof", ["get", k]], "number"];
    const legend = document.getElementById("legend");
    const drawLegend = () => {
      const [, br, f, title] = LAYERS[state.layer];
      const labels = [`under ${f(br[0])}`, ...br.slice(0, -1).map((v, i) => `${f(v)}–${f(br[i + 1])}`), `${f(br[3])} or more`];
      legend.innerHTML = `<div class="title">${title}</div>` +
        labels.map((t, i) => `<div class="row"><span class="swatch" style="background:${RAMP[i]}"></span>${t}</div>`).join("");
    };
    drawLegend();

    const map = new maplibregl.Map({
      container: "map",
      style: `https://basemaps.cartocdn.com/gl/${dark ? "dark-matter" : "positron"}-gl-style/style.json`,
      bounds: site.network.bounds,
      fitBoundsOptions: { padding: 24 },
      attributionControl: { compact: true },
      cooperativeGestures: true,   // the page scrolls past the map; ctrl/two-finger to zoom it
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    map.on("load", () => {
      map.addSource("roads", {
        type: "geojson", data: new URL("data/map.geojson", location.href).href,
        attribution: '<a href="https://kyfromabove.ky.gov/">KyFromAbove</a> lidar; KYTC road network',
      });
      const firstSymbol = map.getStyle().layers.find((l) => l.type === "symbol")?.id;
      map.addLayer({
        id: "sel", type: "line", source: "roads", filter: ["==", ["get", "r"], ""],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": token("--ink"), "line-opacity": 0.25, "line-width": ["interpolate", ["linear"], ["zoom"], 8, 7, 14, 14] },
      }, firstSymbol);
      map.addLayer({
        id: "roads", type: "line", source: "roads", filter: hasValue(state.layer),
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": colorBy(state.layer), "line-width": ["interpolate", ["linear"], ["zoom"], 8, 1.6, 11, 2.6, 14, 4] },
      }, firstSymbol);
      state.ready = true;
      highlight(state.route);
    });

    function setLayer(k) {
      state.layer = k;
      seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.k === k)));
      if (state.ready) {
        map.setFilter("roads", hasValue(k));
        map.setPaintProperty("roads", "line-color", colorBy(k));
      }
      drawLegend();
    }
    function highlight(route) {
      if (state.ready) map.setFilter("sel", ["==", ["get", "r"], route || ""]);
    }

    const popup = new maplibregl.Popup({ maxWidth: "300px" });
    const pickBox = (pt, pad = 6) => [[pt.x - pad, pt.y - pad], [pt.x + pad, pt.y + pad]];
    map.on("mousemove", (e) => {
      map.getCanvas().style.cursor = state.ready && map.queryRenderedFeatures(pickBox(e.point), { layers: ["roads"] }).length ? "pointer" : "";
    });
    map.on("click", (e) => {
      if (!state.ready) return;
      const feats = map.queryRenderedFeatures(pickBox(e.point), { layers: ["roads"] });
      if (!feats.length) return;
      const p = feats[0].properties;
      const v = (k) => (p[k] == null ? "–" : LAYERS[k][2](Number(p[k])));
      popup.setLngLat(e.lngLat).setHTML(`<div class="popup"><div class="big">${esc(routeLabel(p.r))}, mp ${Number(p.m).toFixed(1)}–${(Number(p.m) + 0.1).toFixed(1)}</div>` +
        `<div><span class="k">Friction demand</span> ${v("mu")}</div><div><span class="k">To stop in sight</span> ${v("ms")}</div>` +
        `<div><span class="k">Steepest grade</span> ${v("g")}</div><div><span class="k">Sharpest curve</span> ${v("d")}</div>` +
        `<div><span class="k">Slowest speed</span> ${p.v == null ? "–" : `${Number(p.v)} mph`}</div>` +
        `<div class="k" style="margin-top:4px">Profile below</div></div>`).addTo(map);
      showRoute(p.r);
    });

    const routes = new Map();
    async function showRoute(route) {
      state.route = route;
      highlight(route);
      if (!routes.has(route)) routes.set(route, getJSON(`data/routes/${route}.json`).catch((err) => { routes.delete(route); throw err; }));
      const r = await routes.get(route);
      if (state.route === route) renderProfile(r);
    }
    showRoute(site.featured);
  }

  // ======================= route profiles =======================
  function renderProfile(r) {
    const name = r.name && !r.name.replace(/\D/g, "").includes(String(Number(r.route.split("-")[2].replace(/\D/g, ""))))
      ? ` (${r.name.toLowerCase().replace(/\b\w/g, (ch) => ch.toUpperCase())})` : "";
    const P = r.profile, n = P.z.length;
    const x1 = r.x0 + n * r.dx;
    document.getElementById("profileTitle").textContent = `${routeLabel(r.route)}${name}, ${r.county} County`;
    document.getElementById("profileSub").textContent = `Milepoints ${r.x0.toFixed(2)} to ${x1.toFixed(2)}. ` +
      (r.ways.length === 2 ? "A two-way road: demand is shown for each direction."
        : `One carriageway of a divided highway, driven ${r.ways[0] === "inv" ? "with" : "against"} the milepoints.`);

    // average down to at most ~700 points so long routes stay legible
    const k = Math.max(1, Math.ceil(n / 700));
    const xs = [];
    for (let i = 0; i < n; i += k) xs.push(r.x0 + (i + k / 2) * r.dx);
    const down = (vals, how = "mean") => {
      const out = [];
      for (let i = 0; i < n; i += k) {
        const c = vals.slice(i, i + k).filter((v) => v != null);
        if (!c.length) { out.push(null); continue; }
        if (how === "max") out.push(Math.max(...c));
        else if (how === "absmax") out.push(c.reduce((a, b) => (Math.abs(b) > Math.abs(a) ? b : a)));
        else out.push(c.reduce((a, b) => a + b, 0) / c.length);
      }
      return out;
    };
    const fill = (records, value) => {   // records [m0, m1, ...] to a value per profile sample
      const out = new Array(n).fill(null);
      for (const rec of records) {
        const v = value(rec);
        if (v == null) continue;
        for (let i = Math.max(0, Math.ceil((rec[0] - r.x0) / r.dx)); i <= Math.min(n - 1, Math.floor((rec[1] - r.x0) / r.dx)); i++) out[i] = v;
      }
      return out;
    };
    const range = (series, pad, step, floor) => {
      const all = series.flatMap((s) => s.ys.filter((v) => v != null));
      const lo = Math.min(...all), hi = Math.max(...all);
      return [floor ?? Math.floor((lo - pad) / step) * step, Math.ceil((hi + pad) / step) * step];
    };
    const xAxis = { min: r.x0, max: x1, ticks: niceTicks(r.x0, x1, mileStep(x1 - r.x0)), label: "milepoint" };
    const accent = token("--accent"), muted = token("--context"), ink2 = token("--ink-2");
    const host = document.getElementById("profileCharts");
    host.innerHTML = "";
    const tip = (series, unit, d = 1) => (i, x) => `<b>mp ${x.toFixed(2)}</b><br>` + series.map((s) =>
      `<i style="background:${s.color}"></i>${s.name} ${s.ys[i] == null ? "–" : `${s.ys[i].toFixed(d)}${unit}`}`).join("<br>");

    // elevation: the road, and the bare earth where it falls away under a bridge
    let card = cardIn(host, "Elevation", "Feet above sea level." +
      (r.bridges.length ? " Bars above: KYTC's recorded bridges, decked over in the road's line." : "") +
      (P.bare.some((v) => v != null) ? " The bare-earth line shows only where it falls away from the road." : ""));
    let series = [{ name: "Road", label: "Road", color: accent, xs, ys: down(P.z) },
      { name: "Bare earth", label: "Bare earth", color: muted, xs, ys: down(P.bare, "mean"), width: 1.5 }];
    let [lo, hi] = range(series, 10, 50);
    lineChart(card, { H: 210, aria: `Elevation along ${routeLabel(r.route)}.`, x: xAxis,
      y: { min: lo, max: hi, ticks: niceTicks(lo, hi, hi - lo > 600 ? 200 : hi - lo > 250 ? 100 : 50) },
      bars: r.bridges.map(([a, b]) => ({ x0: a, x1: b, fill: ink2 })), series, tip: tip(series, " ft", 0) });

    // grade: the lidar against KYTC's records
    const kytcGrade = down(fill(r.grades, ([, , dir, g]) => (g == null || !["U", "D"].includes(dir) ? null : dir === "U" ? g : -g)));
    card = cardIn(host, "Grade", "Percent, rising with the milepoints. " + (kytcGrade.some((v) => v != null)
      ? "KYTC's records are uphill and downhill tangents; crests and sags have no single grade."
      : "KYTC's grade inventory has no uphill or downhill tangents on this route."));
    series = [{ name: "Lidar", label: "Lidar", color: accent, xs, ys: down(P.grade) },
      { name: "KYTC", label: "KYTC", color: muted, xs, width: 1.5, ys: kytcGrade }];
    [lo, hi] = range(series, 1, 2);
    lo = Math.max(lo, -16); hi = Math.min(hi, 16);
    lineChart(card, { H: 190, aria: `Grade along ${routeLabel(r.route)}.`, x: xAxis,
      y: { min: lo, max: hi, ticks: niceTicks(lo, hi, hi - lo > 16 ? 4 : 2), fmt: (t) => `${t}%` },
      series, tip: tip(series, "%") });

    // curvature: signed degree of curve, with KYTC's recorded curves above
    card = cardIn(host, "Curvature",
      "Degree of curve, positive turning left with the milepoints. Bars above: KYTC's recorded curves. Shading: curves found here.");
    series = [{ name: "Curvature", color: accent, xs, ys: down(P.deg, "absmax") }];
    [lo, hi] = range(series, 2, 10);
    const top = Math.min(Math.max(Math.abs(lo), Math.abs(hi)), 80);
    lineChart(card, { H: 200, labels: false, aria: `Curvature along ${routeLabel(r.route)}.`, x: xAxis,
      y: { min: -top, max: top, ticks: niceTicks(-top, top, top > 40 ? 20 : 10), fmt: (t) => `${t}°` },
      bars: r.curves.map(([a, b]) => ({ x0: a, x1: b, fill: ink2 })),
      spans: r.found_curves.map(([a, b]) => ({ x0: a, x1: b, fill: accent, opacity: 0.12 })),
      series, tip: tip(series, "°") });

    // friction demand, each way traffic drives, and to stop within the sight left
    card = cardIn(host, "Friction demand", "Share of the car's weight the tires must deliver, braking and cornering combined, " +
      "at the speed the road is likely driven; dashed, to stop within the sight distance left.");
    const WAY = { inv: ["With the milepoints", token("--s1")], non: ["Against them", token("--s2")] };
    series = r.ways.map((w) => ({ name: WAY[w][0], label: r.ways.length > 1 ? WAY[w][0] : "Demand", color: WAY[w][1], xs,
      ys: down(P[w].mu, "max") }));
    const stop = new Array(n).fill(null).map((_, i) => Math.max(...r.ways.map((w) => P[w].mu_stop[i] ?? -1)));
    series.push({ name: "To stop in sight", label: "To stop in sight", color: muted, dash: true, width: 1.5, xs,
      ys: down(stop.map((v) => (v < 0 ? null : v)), "max") });
    const yTop = Math.min(1, Math.ceil(Math.max(0.3, ...series.flatMap((s) => s.ys.filter((v) => v != null))) * 10) / 10);
    lineChart(card, { H: 210, aria: `Friction demand along ${routeLabel(r.route)}.`, x: xAxis,
      y: { min: 0, max: yTop, ticks: niceTicks(0, yTop, yTop > 0.6 ? 0.2 : 0.1), fmt: (t) => t.toFixed(1) },
      series, tip: tip(series, "", 2) });
  }

  // ======================= a small SVG chart kit =======================
  function svgIn(host, W, H, aria) {
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", aria);
    host.appendChild(svg);
    const add = (tag, attrs, text, parent = svg) => {
      const el = document.createElementNS(svgNS, tag);
      for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
      if (text !== undefined) el.textContent = text;
      parent.appendChild(el);
      return el;
    };
    return { svg, add };
  }

  // nearest index in an ascending array
  function nearest(xs, v) {
    let lo = 0, hi = xs.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (xs[mid] < v) lo = mid; else hi = mid;
    }
    return Math.abs(xs[lo] - v) <= Math.abs(xs[hi] - v) ? lo : hi;
  }

  // Line chart: series {name, color, xs, ys (null = gap), dash, width, label}; spans shade x ranges,
  // bars mark x ranges along the top; tip(i0, x) returns the hover tooltip's HTML.
  function lineChart(host, o) {
    const W = o.W || 820, H = o.H || 250;
    const m = { l: 44, r: o.labels === false ? 16 : 110, t: o.bars?.length ? 18 : 12, b: 32, ...o.m };
    const X = (v) => m.l + ((v - o.x.min) / (o.x.max - o.x.min)) * (W - m.l - m.r);
    const Y = (v) => m.t + (1 - (v - o.y.min) / (o.y.max - o.y.min)) * (H - m.t - m.b);
    const { svg, add } = svgIn(host, W, H, o.aria);
    for (const t of o.y.ticks) {
      add("line", { class: "grid", x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t) });
      add("text", { x: m.l - 6, y: Y(t) + 4, "text-anchor": "end" }, o.y.fmt ? o.y.fmt(t) : t);
    }
    for (const t of o.x.ticks) add("text", { x: X(t), y: H - m.b + 16, "text-anchor": "middle" }, o.x.fmt ? o.x.fmt(t) : t);
    if (o.x.label) add("text", { x: W - m.r, y: H - 2, "text-anchor": "end" }, o.x.label);
    if (o.y.label) add("text", { x: m.l, y: m.t - 3 }, o.y.label);
    add("line", { class: "axis", x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b });
    for (const r of o.refs || []) {
      add("line", { class: "ref", x1: m.l, x2: W - m.r, y1: Y(r.y), y2: Y(r.y) });
      add("text", { class: "ref-label", x: m.l + 6, y: Y(r.y) - 5 }, r.label);
    }
    for (const s of o.spans || []) {
      const x0 = X(Math.max(s.x0, o.x.min)), x1 = X(Math.min(s.x1, o.x.max));
      if (x1 > x0) add("rect", { x: x0, y: m.t, width: Math.max(x1 - x0, 1), height: H - m.t - m.b, fill: s.fill, opacity: s.opacity ?? 0.14 });
    }
    for (const b of o.bars || []) {
      const x0 = X(Math.max(b.x0, o.x.min)), x1 = X(Math.min(b.x1, o.x.max));
      if (x1 > x0) add("rect", { x: x0, y: m.t - 10, width: Math.max(x1 - x0, 2), height: 4, rx: 2, fill: b.fill });
    }
    for (const s of o.series) {
      let d = "", pen = false;
      s.xs.forEach((x, i) => {
        const y = s.ys[i];
        if (y == null) { pen = false; return; }
        d += `${pen ? "L" : "M"}${X(x).toFixed(1)},${Y(Math.max(o.y.min, Math.min(o.y.max, y))).toFixed(1)}`;
        pen = true;
      });
      add("path", { class: "line", d, stroke: s.color, "stroke-width": s.width || 2, ...(s.dash ? { "stroke-dasharray": "5 4" } : {}) });
    }
    // direct labels at each labelled series' last point, nudged apart
    const ends = o.series.filter((s) => s.label).map((s) => {
      let i = s.ys.length - 1;
      while (i >= 0 && s.ys[i] == null) i--;
      return i < 0 ? null : { s, y: Y(Math.max(o.y.min, Math.min(o.y.max, s.ys[i]))) };
    }).filter(Boolean).sort((a, b) => a.y - b.y);
    for (let k = 1; k < ends.length; k++) ends[k].y = Math.max(ends[k].y, ends[k - 1].y + 13);
    for (const e of ends) add("text", { class: "end-label", x: W - m.r + 6, y: e.y + 4 }, e.s.label);

    // hover: crosshair, a dot per series, and the tooltip
    const cross = add("line", { class: "cross", y1: m.t, y2: H - m.b, visibility: "hidden" });
    const dots = o.series.map((s) => add("circle", { r: 3.5, fill: s.color, stroke: token("--surface"), "stroke-width": 1.5, visibility: "hidden" }));
    const hit = add("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent" });
    const tip = document.createElement("div");
    tip.className = "tip";
    tip.hidden = true;
    host.appendChild(tip);
    const ref = o.series[0].xs;
    hit.addEventListener("pointermove", (evt) => {
      const box = svg.getBoundingClientRect();
      const sx = ((evt.clientX - box.left) / box.width) * W;
      const xv = o.x.min + ((sx - m.l) / (W - m.l - m.r)) * (o.x.max - o.x.min);
      const i0 = nearest(ref, xv);
      const px = X(ref[i0]);
      cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
      o.series.forEach((s, k) => {
        const i = nearest(s.xs, ref[i0]), y = s.ys[i];
        dots[k].setAttribute("visibility", y == null ? "hidden" : "visible");
        if (y != null) { dots[k].setAttribute("cx", X(s.xs[i])); dots[k].setAttribute("cy", Y(Math.max(o.y.min, Math.min(o.y.max, y)))); }
      });
      tip.hidden = false;
      tip.innerHTML = o.tip(i0, ref[i0]);
      const left = (px / W) * box.width + 14;
      tip.style.left = `${left + tip.offsetWidth > box.width ? left - tip.offsetWidth - 28 : left}px`;
      tip.style.top = `${12 + (m.t / H) * box.height}px`;
    });
    hit.addEventListener("pointerleave", () => {
      cross.setAttribute("visibility", "hidden");
      dots.forEach((dt) => dt.setAttribute("visibility", "hidden"));
      tip.hidden = true;
    });
  }

  function niceTicks(lo, hi, step) {
    const out = [];
    for (let k = Math.ceil(lo / step - 1e-9); k * step <= hi + 1e-9; k++) out.push(+(k * step).toFixed(6));
    return out;
  }
  function mileStep(span) {
    return span > 40 ? 10 : span > 16 ? 5 : span > 6 ? 2 : span > 2.5 ? 0.5 : span > 1 ? 0.25 : 0.05;
  }

  function cardIn(host, title, sub) {
    const card = document.createElement("div");
    card.className = "chart";
    card.innerHTML = `<h4>${title}</h4>${sub ? `<p class="sub">${sub}</p>` : ""}`;
    host.appendChild(card);
    return card;
  }
})();
