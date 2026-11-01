"use strict";
let DATA, S, V, P, D, T;
async function load() {
  const bin = atob(document.getElementById("data").textContent.trim());
  const u = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
  const txt = await new Response(
    new Blob([u]).stream().pipeThrough(new DecompressionStream("gzip")),
  ).text();
  DATA = JSON.parse(txt);
  S = DATA.S;
  V = DATA.V;
  P = DATA.P;
  D = DATA.D;
  T = DATA.T;
}
const TR = [
  "link",
  "typed",
  "bookmark",
  "subframe",
  "subframe",
  "generated",
  "toplevel",
  "form",
  "reload",
  "keyword",
  "keyword",
];
const $ = (s) => document.querySelector(s);
const esc = (s) =>
  String(s).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const pad = (n) => String(n).padStart(2, "0");
const hm = (t) => {
  const d = new Date(t * 1000);
  return pad(d.getHours()) + ":" + pad(d.getMinutes());
};
function domain(u) {
  try {
    const x = new URL(u);
    return x.hostname.replace(/^www\./, "") || x.protocol.replace(":", "");
  } catch (e) {
    return "";
  }
}
function hue(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h) % 360;
}
function chip(u) {
  const d = domain(u);
  return `<span class="chip" style="background:hsl(${hue(d)},45%,45%)" title="${esc(d)}">${esc((d || "?")[0])}</span>`;
}
function link(u, t) {
  const d = domain(u);
  const safe = /^(https?|ftp|file|chrome|about):/i.test(u) ? u : "#";
  return `<span class="ti"><a href="${esc(safe)}" target="_blank" rel="noreferrer noopener" title="${esc(u)}">${esc(t || u)}</a><span class="dom">${esc(d)}</span></span>`;
}
load()
  .then(init)
  .catch((e) => {
    document.getElementById("main").textContent = "Failed to load data: " + e;
  });
const byProf = {};
let snapDays = new Set();
function init() {
  document.getElementById("gen").textContent =
    "updated " + new Date(DATA.generated * 1000).toLocaleString();

  // snapshots by profile, sorted by day, for "as of" lookups (carry forward)
  T.forEach((s) => (byProf[s.p] = byProf[s.p] || []).push(s));
  Object.values(byProf).forEach((a) =>
    a.sort((x, y) => (x.d < y.d ? -1 : x.d > y.d ? 1 : 0)),
  );
  snapDays = new Set(T.map((s) => s.d));
  route();
}
function snapAsOf(p, day) {
  const a = byProf[p] || [];
  let r = null;
  for (const s of a) {
    if (s.d <= day) r = s;
    else break;
  }
  return r;
}

// day list
function renderDays(sel) {
  let h = "",
    m = "";
  for (const [d, a, b] of D) {
    const mo = d.slice(0, 7);
    if (mo !== m) {
      m = mo;
      h += `<div class="month">${new Date(d + "T12:00").toLocaleDateString(undefined, { year: "numeric", month: "long" })}</div>`;
    }
    const wd = new Date(d + "T12:00").toLocaleDateString(undefined, {
      weekday: "short",
    });
    h += `<a href="#${d}" class="${d === sel ? "sel" : ""}"><span>${d.slice(8)} ${wd}${snapDays.has(d) ? " •" : ""}</span><span class="c">${b - a || ""}</span></a>`;
  }
  $("#days").innerHTML = h;
  const s = $("#days a.sel");
  if (s) s.scrollIntoView({ block: "nearest" });
}

function renderDay(day) {
  const row = D.find((r) => r[0] === day);
  if (!row) {
    $("#main").innerHTML = '<p class="empty">No data for this day.</p>';
    return;
  }
  const [, a, b] = row;
  let h = `<h2>${new Date(day + "T12:00").toLocaleDateString(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" })}</h2>`;
  h += `<div class="sub">${b - a} history visits</div><h3>Open tabs as of this day</h3>`;
  let any = false;
  P.forEach((pn, p) => {
    const s = snapAsOf(p, day);
    if (!s) return;
    any = true;
    const n = s.w.reduce((x, w) => x + w.length, 0),
      nn = s.w.reduce((x, w) => x + w.filter((t) => t[3]).length, 0);
    const when =
      s.d === day
        ? `snapshot ${hm(s.t)}, seen until ${new Date(s.s * 1000).toLocaleString()}`
        : `carried forward from ${s.d}`;
    h += `<details open><summary>${esc(pn)}<span class="sub">${n} tabs · ${s.w.length} windows${nn ? ` · ${nn} new` : ""} · ${esc(when)}</span></summary>`;
    s.w.forEach((w, wi) => {
      h += `<details ${s.w.length <= 12 ? "open" : ""} style="margin:6px 10px"><summary>Window ${wi + 1}<span class="sub">${w.length} tabs${w.some((t) => t[3]) ? ` · ${w.filter((t) => t[3]).length} new` : ""}</span></summary><ul>`;
      for (const [u, t, pin, nw] of w) {
        h += `<li class="${nw ? "new" : ""}">${chip(S[u])}${link(S[u], S[t])}${pin ? '<span class="pin">pinned</span>' : ""}${nw ? '<span class="badge">NEW</span>' : ""}</li>`;
      }
      h += "</ul></details>";
    });
    h += "</details>";
  });
  if (!any)
    h +=
      '<p class="empty">No tab snapshot existed yet on this day (snapshots start when tab-capsule was installed).</p>';
  h += `<h3>History</h3><div class="tools"><button data-x="open">expand all</button><button data-x="close">collapse all</button></div>`;
  if (b === a) h += '<p class="empty">No visits.</p>';
  const hours = {};
  for (let i = a; i < b; i++) {
    const hr = new Date(V[i][0] * 1000).getHours();
    (hours[hr] = hours[hr] || []).push(i);
  }
  Object.keys(hours)
    .map(Number)
    .sort((x, y) => y - x)
    .forEach((hr) => {
      const idx = hours[hr];
      h += `<details class="hr"><summary>${pad(hr)}:00<span class="sub">${idx.length} visits</span></summary><ul>`;
      for (const i of idx.reverse()) {
        const [t, u, ti, p, tr] = V[i];
        h += `<li><span class="t">${hm(t)}</span>${chip(S[u])}${link(S[u], S[ti])}${P.length > 1 ? `<span class="prof">${esc(P[p])}</span>` : ""}<span class="prof">${TR[tr] || ""}</span></li>`;
      }
      h += "</ul></details>";
    });
  $("#main").innerHTML = h;
  $("#main")
    .querySelectorAll(".tools button")
    .forEach(
      (btn) =>
        (btn.onclick = () =>
          $("#main")
            .querySelectorAll("details.hr")
            .forEach((d) => (d.open = btn.dataset.x === "open"))),
    );
  window.scrollTo(0, 0);
}

// search: match the string table once, then scan visits/snapshots by index
const MAXV = 300,
  MAXT = 100;
function search(q) {
  const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
  const hit = new Uint8Array(S.length);
  for (let i = 0; i < S.length; i++) {
    const s = S[i].toLowerCase();
    if (terms.some((t) => s.includes(t))) hit[i] = 1;
  }
  const ok = (u, t) => {
    const s = (S[u] + " " + S[t]).toLowerCase();
    return terms.every((x) => s.includes(x));
  };
  let vh = [],
    more = 0;
  for (let i = V.length - 1; i >= 0; i--) {
    const v = V[i];
    if ((hit[v[1]] || hit[v[2]]) && ok(v[1], v[2])) {
      if (vh.length < MAXV) vh.push(i);
      else more++;
    }
  }
  const seen = new Map();
  for (let k = T.length - 1; k >= 0; k--) {
    const s = T[k];
    for (const w of s.w)
      for (const tab of w) {
        if ((hit[tab[0]] || hit[tab[1]]) && ok(tab[0], tab[1])) {
          const key = s.p + "|" + tab[0];
          const prev = seen.get(key);
          if (!prev || prev.d < s.d)
            seen.set(key, { d: s.d, u: tab[0], t: tab[1], p: s.p });
        }
      }
  }
  const th = [...seen.values()]
    .sort((x, y) => (x.d < y.d ? 1 : -1))
    .slice(0, MAXT);
  let h = `<h2>Search: ${esc(q)}</h2><div class="sub">${vh.length + more} visits${more ? ` (showing newest ${MAXV})` : ""} · ${seen.size} open-tab matches</div>`;
  if (th.length) {
    h += `<h3>In open-tab snapshots</h3><details open><summary>Tabs<span class="sub">last seen day</span></summary><ul>`;
    for (const r of th)
      h += `<li>${chip(S[r.u])}${link(S[r.u], S[r.t])}<span class="prof">${esc(P[r.p])}</span><button class="jump" data-d="${r.d}">${r.d}</button></li>`;
    h += "</ul></details>";
  }
  h += `<h3>In history</h3>`;
  if (!vh.length) h += '<p class="empty">No visits match.</p>';
  else {
    h +=
      "<details open><summary>Visits<span class=sub>newest first</span></summary><ul>";
    for (const i of vh) {
      const [t, u, ti, p] = V[i];
      const d = new Date(t * 1000);
      const day =
        d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
      h += `<li><button class="jump" data-d="${day}">${day}</button><span class="t">${hm(t)}</span>${chip(S[u])}${link(S[u], S[ti])}${P.length > 1 ? `<span class="prof">${esc(P[p])}</span>` : ""}</li>`;
    }
    h += "</ul></details>";
  }
  $("#main").innerHTML = h;
  $("#main")
    .querySelectorAll(".jump")
    .forEach(
      (b) =>
        (b.onclick = () => {
          $("#q").value = "";
          location.hash = b.dataset.d;
          route();
        }),
    );
}

function route() {
  const q = $("#q").value.trim();
  if (q.length >= 2) {
    renderDays(null);
    search(q);
    return;
  }
  let d = decodeURIComponent(location.hash.slice(1));
  if (!D.some((r) => r[0] === d)) d = D.length ? D[0][0] : "";
  renderDays(d);
  if (d) renderDay(d);
  else $("#main").innerHTML = '<p class="empty">No data yet.</p>';
}
let timer = null;
$("#q").addEventListener("input", () => {
  clearTimeout(timer);
  timer = setTimeout(route, 200);
});
window.addEventListener("hashchange", () => {
  if (!$("#q").value.trim()) route();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== $("#q")) {
    e.preventDefault();
    $("#q").focus();
  }
  if (e.key === "Escape" && document.activeElement === $("#q")) {
    $("#q").value = "";
    route();
  }
});
