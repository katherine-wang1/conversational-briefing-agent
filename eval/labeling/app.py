"""Streamlit labelling app (TDD §18.3).

    pip install -e ".[eval]"
    streamlit run eval/labeling/app.py

Reads items from `eval/data/labels/items_<L1|L2|L3>.jsonl` (built with `python -m eval.labeling.build`),
appends labels to `eval/data/labels/<session>.jsonl`, and shows agreement (accuracy, Cohen's kappa)
against `eval/data/labels/judge_<kind>.jsonl` on the Agreement tab. Model labels are never shown
while labelling.
"""

from __future__ import annotations

from collections import defaultdict

from eval.labeling.store import (
    LABELS_DIR,
    LabelRecord,
    agreement,
    append_label,
    next_unlabelled,
    read_items,
    read_judge_labels,
    read_labels,
    session_paths,
)


def _render_payload(st, item) -> None:  # noqa: ANN001 - streamlit module
    p = item.payload
    if item.kind in ("topic", "importance"):
        st.subheader(p["title"])
        if p.get("summary"):
            st.write(p["summary"])
        if p.get("first_seen"):
            st.caption(f"first seen {p['first_seen']}")
        st.markdown("**Which subtopic fits best?**" if item.kind == "topic" else "**Is this big news?**")
    elif item.kind == "relevance":
        st.text(p["profile"])
        st.subheader(p["headline"])
        st.write(p.get("summary", ""))
        st.markdown(
            "**How relevant is this story to this reader?** (1 = unrelated, 5 = exactly their interest)"
        )
    elif item.kind == "claim":
        st.markdown(f"**Claim:** {p['claim']}")
        st.caption(f"from: {p.get('sentence', '')}")
        for pid, text in p["passages"]:
            st.info(f"[{pid}] {text}")
        st.markdown("**Is the claim supported by the cited passages only?**")
    elif item.kind == "qa":
        st.markdown(f"**Question:** {p['question']}")
        st.markdown(f"**Answer:** {p['answer']}")
        for pid, text in p["passages"]:
            st.info(f"[{pid}] {text}")
        st.caption(f"answerability label: {p.get('answerability', '')}")


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="Briefing agent labels", layout="centered")
    session = st.sidebar.selectbox("Session", ["L1", "L2", "L3"])
    labeller = st.sidebar.text_input("Labeller", value="kate")
    items_path, labels_path = session_paths(session)
    items = read_items(items_path)
    labels = read_labels(labels_path)
    tab_label, tab_agree = st.tabs(["Label", "Agreement"])

    with tab_label:
        if not items:
            st.warning(f"No items at {items_path}. Build them with `python -m eval.labeling.build`.")
        else:
            done = sum(1 for i in items if i.item_id in labels)
            st.progress(done / len(items), text=f"{done} / {len(items)} labelled")
            item = next_unlabelled(items, labels)
            if item is None:
                st.success("All items in this session are labelled.")
            else:
                _render_payload(st, item)
                with st.form(key=item.item_id):
                    choice = st.radio("Label", item.options, index=None, horizontal=item.kind != "topic")
                    note = st.text_input("Note (optional)")
                    if st.form_submit_button("Save and next") and choice is not None:
                        append_label(
                            LabelRecord(
                                item_id=item.item_id,
                                session=session,
                                kind=item.kind,
                                label=choice,
                                labeller=labeller,
                                note=note,
                            ),
                            labels_path,
                        )
                        st.rerun()

    with tab_agree:
        by_kind: dict[str, dict[str, str]] = defaultdict(dict)
        for r in labels.values():
            by_kind[r.kind][r.item_id] = r.label
        if not by_kind:
            st.info("No labels yet.")
        for kind, human in by_kind.items():
            judge = read_judge_labels(LABELS_DIR / f"judge_{kind}.jsonl")
            if not judge:
                st.write(f"{kind}: {len(human)} human labels; no judge labels yet.")
                continue
            a = agreement(kind, human, judge)
            st.metric(
                f"{kind}: n={a.n}",
                f"acc {a.accuracy:.2f}" if a.accuracy is not None else "-",
                f"kappa {a.kappa:.2f}" if a.kappa is not None else "",
            )
            if a.passes_bar is False:
                st.error("kappa < 0.6: revise the judge prompt or model and re-validate on fresh items.")
            st.json(a.confusion)


if __name__ == "__main__":
    main()
