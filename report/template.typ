// Typst template for the m_rk3576 SI reports.
// build_report.py writes a report.typ that imports this file and calls
// `report(meta, summary, config, cases)`.

#let ink = rgb("#0b0b0b")
#let ink2 = rgb("#52514e")
#let muted = rgb("#8b8a84")
#let rule = rgb("#e2e1dc")
#let verdict-colour(v) = (
  PASS: rgb("#0ca30c"), MARGINAL: rgb("#c98500"),
  FAIL: rgb("#d03b3b"), INVALID: rgb("#8b8a84"), ERROR: rgb("#8b2cb3"),
).at(v, default: ink2)

#let verdict-chip(v) = box(
  fill: verdict-colour(v).lighten(82%), stroke: 0.5pt + verdict-colour(v),
  radius: 3pt, inset: (x: 5pt, y: 2pt),
  text(fill: verdict-colour(v).darken(20%), weight: "semibold", size: 8.5pt, v),
)

#let kv-table(rows) = table(
  columns: (auto, 1fr), stroke: (x, y) => (bottom: 0.4pt + rule),
  inset: (x: 6pt, y: 4pt), align: (left, left),
  ..rows.map(r => (text(fill: ink2, r.at(0)), r.at(1))).flatten(),
)

#let num-table(header, rows) = table(
  columns: header.len(), align: (x, y) => if x == 0 { left } else { right },
  stroke: (x, y) => (bottom: 0.4pt + rule), inset: (x: 6pt, y: 3.5pt),
  fill: (x, y) => if y == 0 { rule.lighten(50%) } else { none },
  table.header(..header.map(h => text(weight: "semibold", size: 8.5pt, h))),
  ..rows.flatten().map(c => text(size: 8.5pt, c)),
)

#let case-page(c) = {
  pagebreak(weak: true)
  block(below: 2pt)[
    #text(size: 14pt, weight: "semibold")[#c.title]
    #h(8pt) #verdict-chip(c.verdict)
  ]
  text(fill: ink2, size: 9pt)[
    Nets #c.nets.map(n => raw(n)).join(", ") · netclass #raw(c.netclass)
    · target #c.target · #c.geometry
  ]
  v(6pt)
  if c.verdict == "ERROR" {
    block(fill: verdict-colour("ERROR").lighten(90%), inset: 10pt,
      radius: 4pt, width: 100%)[#text(size: 9.5pt)[#c.note]]
  } else { grid(
    columns: (1fr, 1.25fr), column-gutter: 10pt,
    image(c.slice, width: 100%),
    [
      #num-table(c.header, c.rows)
      #v(4pt)
      #text(size: 8.5pt)[#c.note]
    ],
  )
  v(6pt)
  grid(
    columns: if c.plots.len() <= 4 { 2 } else { 3 },
    column-gutter: 6pt, row-gutter: 4pt,
    ..c.plots.map(p => image(p, width: 100%)),
  ) }
}

#let report(meta, summary, config, cases) = {
  set page(paper: "a4", margin: (x: 16mm, y: 16mm),
    footer: context [
      #set text(size: 7.5pt, fill: muted)
      #meta.title #h(1fr) #counter(page).display("1 / 1", both: true)
    ])
  set text(font: ("Fira Sans", "DejaVu Sans"), size: 9.5pt, fill: ink)
  set par(leading: 0.55em)
  show heading.where(level: 1): it => block(above: 14pt, below: 6pt,
    text(size: 13pt, weight: "semibold", it.body))
  show raw: set text(font: ("Fira Mono", "DejaVu Sans Mono"), size: 8.5pt)

  // cover
  text(size: 22pt, weight: "semibold")[#meta.title]
  v(2pt)
  text(fill: ink2)[#meta.subtitle]
  v(10pt)
  kv-table(meta.facts)
  v(8pt)
  heading(level: 1)[Summary]
  num-table(summary.header, summary.rows)
  v(6pt)
  for p in summary.notes { par(p) }
  heading(level: 1)[What is simulated]
  kv-table(config)

  for c in cases { case-page(c) }
}
