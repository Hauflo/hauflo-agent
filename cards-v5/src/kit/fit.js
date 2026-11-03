// Layer 3 · fit ladder (K3 · G1–G5), run after every render inside a card's root:
// name → smaller step → 2 lines → ellipsis; caps → between words → ellipsis (a word wider than the line → one line + ellipsis);
// status line → its short word (data-short) → icon/dot only → hidden (the slot keeps its height).
export function fit(root) {
  const all = (s) => [...root.querySelectorAll(s)];
  for (const n of all(".hf5-name")) {
    n.classList.remove("is-s", "is-1");
    if (!(n.scrollWidth > n.clientWidth + 1 || n.scrollHeight > n.clientHeight + 1)) continue;
    n.classList.add("is-s");
    if (n.scrollWidth > n.clientWidth + 1) n.classList.add("is-1");
  }
  // caps (block titles, «КОНДИЦИОНЕРЫ» at S): between words; one word wider than the line → one line + ellipsis
  for (const c of all(".hf5-cap")) {
    c.classList.remove("is-1");
    if (c.scrollWidth > c.clientWidth + 1) c.classList.add("is-1");
  }
  for (const l of all(".hf5-line")) {
    const t = l.querySelector(".hf5-t");
    if (!t) continue;
    if (t.dataset.full == null) t.dataset.full = t.textContent;
    else t.textContent = t.dataset.full;
    l.classList.remove("is-bare", "is-gone");
    const over = () => t.scrollWidth > t.clientWidth + 1 || l.scrollWidth > l.clientWidth + 1; // the text shrinks with «…»: measure it
    if (over() && l.dataset.short) t.textContent = l.dataset.short;
    if (over()) l.classList.add(l.querySelector(".hf5-dot") ? "is-bare" : "is-gone");
  }
}
