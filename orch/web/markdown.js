"use strict";
// Mise en forme des réponses (titres, listes, code, gras, citations) — module PUR : il ne
// renvoie que des données. La page les affiche avec textContent / createElement : jamais de
// HTML injecté, donc rien de ce que dit un agent ne peut devenir une balise.
(function (root) {
  const MAX_DEPTH = 3;
  const MAX_SPAN = 400;  // portée maximale d'un code / gras / italique : borne le coût d'un texte hostile
  const OMITTED = "…(début omis) ";  // ce que textutil.tail() met devant une réponse coupée
  const FENCE = /^\s*```([\w+#.-]*)[^`]*$/;  // « ```ls``` du texte » n'est pas une ouverture de bloc
  const CLOSE_FENCE = /^\s*```\s*$/;
  const HEADING = /^(#{1,6})\s+(.+?)\s*$/;
  const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
  const QUOTE = /^>\s?(.*)$/;
  const BULLET = /^(\s*)[-*+]\s+(.*)$/;
  const NUMBER = /^(\s*)\d+[.)]\s+(.*)$/;
  const OPEN_BEFORE = /[\s([{"'>]/;       // avant un * ouvrant : pas « src/*.py » ni « 2*3*4 »
  const CLOSE_AFTER = /[\s.,;:!?)\]}"'<]/;  // après un * fermant
  const isSpace = (c) => c === undefined || /\s/.test(c);
  const okOpen = (s, i) => i === 0 || OPEN_BEFORE.test(s[i - 1]);
  const okClose = (s, end) => end >= s.length || CLOSE_AFTER.test(s[end]);

  // Code en ligne : une suite de n accents graves se ferme par une suite de n exactement.
  function codeSpan(s, i) {
    let n = 1;
    while (s[i + n] === "`") n += 1;
    const mark = "`".repeat(n);
    const win = s.slice(i + n, i + n + MAX_SPAN + n);
    for (let k = win.indexOf(mark); k !== -1; k = win.indexOf(mark, k + 1)) {
      const j = i + n + k;
      if (k > 0 && s[j - 1] !== "`" && s[j + n] !== "`") return { span: { t: "code", v: s.slice(i + n, j) }, end: j + n, run: n };
    }
    return { span: null, end: i + n, run: n };
  }

  // Gras (**) ou italique (*) : bornes prudentes des deux côtés, fermeture cherchée dans MAX_SPAN.
  function emphasis(s, i, width, tag) {
    if (!okOpen(s, i) || isSpace(s[i + width])) return null;
    const win = s.slice(i + width, i + width + MAX_SPAN + width);
    const mark = "*".repeat(width);
    for (let k = win.indexOf(mark); k !== -1; k = win.indexOf(mark, k + 1)) {
      const j = i + width + k, end = j + width;
      const lone = width === 2 || (s[j + 1] !== "*" && s[j - 1] !== "*");
      if (k > 0 && !isSpace(s[j - 1]) && lone && okClose(s, end)) return { span: { t: tag, v: s.slice(i + width, j) }, end };
    }
    return null;
  }

  function parseInline(src) {
    const s = typeof src === "string" ? src : "";
    const out = [];
    let buf = "";
    const flush = () => { if (buf) { out.push({ t: "text", v: buf }); buf = ""; } };
    let i = 0;
    while (i < s.length) {
      const c = s[i];
      let hit = null;
      if (c === "`") {
        const code = codeSpan(s, i);
        hit = code.span ? code : null;
        if (!hit) { buf += "`".repeat(code.run); i += code.run; continue; }
      } else if (c === "*") {
        hit = s[i + 1] === "*" ? emphasis(s, i, 2, "strong") : emphasis(s, i, 1, "em");
        if (!hit && s[i + 1] === "*") { buf += "**"; i += 2; continue; }
      }
      if (hit) { flush(); out.push(hit.span); i = hit.end; continue; }
      buf += c; i += 1;
    }
    flush();
    return out;
  }

  const withBreaks = (lines) => lines.reduce((acc, ln, k) => acc.concat(k ? [{ t: "br" }] : [], parseInline(ln)), []);
  const listKind = (line) => (BULLET.test(line) ? false : NUMBER.test(line) ? true : null);
  const itemOf = (line) => {
    const m = BULLET.exec(line) || NUMBER.exec(line);
    return { depth: Math.min(MAX_DEPTH, Math.floor(m[1].length / 2)), text: m[2] };
  };

  // Réponse coupée par tail() : on le dit, et si la coupure tombe dans un bloc de code (nombre impair
  // de clôtures), le début est du code — sinon toutes les clôtures suivantes seraient lues à l'envers.
  function startOf(src) {
    const text = src.replace(/\r\n?/g, "\n");
    if (!text.startsWith(OMITTED)) return { lines: text.split("\n"), cut: false };
    const lines = text.slice(OMITTED.length).split("\n");
    if (lines.filter((l) => FENCE.test(l)).length % 2 === 1) lines.unshift("```");
    return { lines, cut: true };
  }

  function parseMarkdown(src) {
    if (typeof src !== "string" || !src.trim()) return [];
    const { lines, cut } = startOf(src);
    const blocks = cut ? [{ type: "note", text: "début omis" }] : [];
    let list = null, i = 0;
    const endList = () => { list = null; };

    while (i < lines.length) {
      const line = lines[i];
      let m;
      if (FENCE.test(line)) {
        endList();
        const lang = FENCE.exec(line)[1];
        const body = [];
        i += 1;
        while (i < lines.length && !CLOSE_FENCE.test(lines[i])) { body.push(lines[i]); i += 1; }
        blocks.push({ type: "code", lang, text: body.join("\n") });
        i += 1;  // saute la clôture (absente : fin du texte, le bloc reste affiché)
        continue;
      }
      if (!line.trim()) {  // une suite de lignes vides d'un coup : coût linéaire
        let k = i + 1;
        while (k < lines.length && !lines[k].trim()) k += 1;
        if (list && !(k < lines.length && listKind(lines[k]) === list.ordered)) endList();
        i = k; continue;
      }
      if ((m = HEADING.exec(line))) {
        endList();
        blocks.push({ type: "heading", level: m[1].length, inline: parseInline(m[2]) });
      } else if (RULE.test(line)) {
        endList();
        blocks.push({ type: "hr" });
      } else if (QUOTE.test(line)) {
        endList();
        const quoted = [];
        while (i < lines.length && QUOTE.test(lines[i])) { quoted.push(QUOTE.exec(lines[i])[1]); i += 1; }
        blocks.push({ type: "quote", inline: withBreaks(quoted) });
        continue;
      } else if (line.trim().startsWith("|")) {
        endList();
        const rows = [];
        while (i < lines.length && lines[i].trim().startsWith("|")) { rows.push(lines[i].trim()); i += 1; }
        blocks.push({ type: "code", lang: "table", text: rows.join("\n") });
        continue;
      } else if (listKind(line) !== null) {
        const ordered = listKind(line);
        if (!list || list.ordered !== ordered) {
          list = { type: "list", ordered, items: [] };
          blocks.push(list);
        }
        const it = itemOf(line);
        list.items.push({ depth: it.depth, inline: parseInline(it.text) });
      } else if (list && /^\s{2,}\S/.test(line)) {  // suite d'un élément de liste
        const last = list.items[list.items.length - 1];
        last.inline = last.inline.concat([{ t: "br" }], parseInline(line.trim()));
      } else {  // une ligne = un paragraphe : un texte brut en lignes n'est pas un seul bloc
        endList();
        blocks.push({ type: "para", inline: parseInline(line) });
      }
      i += 1;
    }
    return blocks;
  }

  const api = { parseMarkdown, parseInline };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Markdown = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
