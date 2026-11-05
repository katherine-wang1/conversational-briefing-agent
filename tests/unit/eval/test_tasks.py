"""Test content and task builders: injection set, T3 question generation, hindsight labels."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.config import loader
from app.contracts.brief import Fact, Passage, SourcePack
from app.contracts.sources import HeadlineObservation
from eval.graders.injection import grade_injection_case
from eval.runner.fakes import FakeModelClient
from eval.tasks import hindsight as hs
from eval.tasks import t3_qgen
from eval.tasks.injection.load import load_injection_set, summary, to_archive

# ---- injection set ----


def test_injection_set_shape():
    docs = load_injection_set()
    assert len(docs) == 50
    assert len({d.id for d in docs}) == 50
    assert len({d.canary for d in docs}) == 50
    s = summary(docs)["style"]
    assert set(s) == {
        "direct",
        "hidden_text",
        "fake_system",
        "tool_bait",
        "authority",
        "exfiltration",
        "score_manipulation",
    }
    assert min(s.values()) >= 3
    ai = {t.id for t in loader.taxonomy().subtopics if t.beat == "ai"}
    for d in docs:
        assert "{CANARY}" not in d.body + d.planted_instruction
        assert d.canary in d.body, d.id  # the planted instruction carries the canary
        assert d.source.endswith(".invalid") and d.subtopic in ai
        assert d.expected_safe_behavior
        for f in d.forbidden_tool_calls:
            assert "{CANARY}" not in (f.arg_contains or "")
    assert sum(1 for d in docs if d.forbidden_tool_calls) >= 10


def test_injection_docs_convert_to_archive_and_grade():
    docs = load_injection_set()
    start = datetime(2026, 10, 1, tzinfo=UTC)
    documents, obs = to_archive(docs, start)
    assert len(documents) == 50 and all(o.observed_at >= start for o in obs)
    assert all(doc.available_at.hour == 5 for doc in documents)
    d = docs[0]
    safe = grade_injection_case(d.id, d.canary, {"brief": "Quarrystone AI raised $62 million."})
    assert not safe.success
    hit = grade_injection_case(d.id, d.canary, {"brief": f"{d.canary} is the best legal AI company"})
    assert hit.success


# ---- T3 question generation ----


def pack() -> SourcePack:
    text = "Acme raised $40 million in a Series B led by Fund X. The company has 120 employees."
    return SourcePack(
        story_ref="st1",
        headline="Acme raises $40M",
        depth="standard",
        facts=[Fact(text="Acme raised $40M", passage_ids=["p1"])],
        passages=[
            Passage(
                passage_id="p1",
                doc_id="d1",
                url="https://example.invalid/a",
                text=text,
                char_start=0,
                char_end=len(text),
            )
        ],
    )


def test_t3_validation():
    p = pack()
    ok = t3_qgen.GenQuestion(
        question="Who led the round?",
        label="answerable",
        supporting_passage_id="p1",
        supporting_quote="a Series B led by Fund X",
    )
    assert t3_qgen.validate(ok, p) is None
    bad_quote = ok.model_copy(update={"supporting_quote": "led by Fund Y"})
    assert "verbatim" in t3_qgen.validate(bad_quote, p)
    bad_cite = t3_qgen.GenQuestion(
        question="What will revenue be next year?", label="unanswerable", supporting_passage_id="p1"
    )
    assert t3_qgen.validate(bad_cite, p)


async def test_t3_generate_for_pack_keeps_only_valid_items():
    client = FakeModelClient(
        {
            "eval.qgen": lambda req: {
                "questions": [
                    {
                        "question": "Who led the round?",
                        "label": "answerable",
                        "supporting_passage_id": "p1",
                        "supporting_quote": "led by Fund X",
                    },
                    {
                        "question": "How many staff do they have?",
                        "label": "answerable",
                        "supporting_passage_id": "p1",
                        "supporting_quote": "200 employees",
                    },  # not verbatim -> rejected
                    {"question": "What will they announce next quarter?", "label": "unanswerable"},
                ]
            }
        }
    )
    items, rejects = await t3_qgen.generate_for_pack(client, pack(), run_id="dev-full-s1", brief_id="b1")
    assert [i.label for i in items] == ["answerable", "unanswerable"]
    assert len(rejects) == 1 and all(not i.human_checked for i in items)
    assert items[0].supporting_text.startswith("Acme raised")
    assert client.requests[0].role == "eval.qgen"
    assert loader.models().for_role("eval.qgen").provider == "tinker"


# ---- hindsight labels ----


class TitleClusterer:
    def cluster(self, items):
        groups: dict[str, list[int]] = {}
        for i, o in enumerate(items):
            groups.setdefault(o.title.split(":")[0], []).append(i)
        return list(groups.values())


def obs_(title: str, url: str, day: int, hours: int = 6) -> HeadlineObservation:
    return HeadlineObservation(
        source_id="x",
        beat="ai",
        url=url,
        title=title,
        observed_at=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(days=day, hours=hours),
    )


def test_domain_normalisation():
    assert hs.domain("https://www.example.com/a") == "example.com"
    assert hs.domain("https://news.bbc.co.uk/x") == "bbc.co.uk"
    assert hs.domain("https://blog.openai.com/x") == "openai.com"


def test_coverage_label_counts_independent_domains_within_14_days():
    obs = [
        obs_("A: launch", "https://a.com/1", 0),
        obs_("A: more", "https://b.com/2", 1),
        obs_("A: x", "https://c.com/3", 2),
        obs_("A: late", "https://d.com/4", 20),  # after 14 days: not counted
        obs_("A: agg", "https://news.ycombinator.com/item?id=1", 0),  # aggregator: not independent
        obs_("B: small", "https://a.com/9", 1),
        obs_("C: two", "https://e.com/1", 2),
        obs_("C: two", "https://f.com/1", 2),
    ]
    rows, rep = hs.build_labels(
        obs,
        TitleClusterer(),
        window=(date(2026, 10, 1), date(2026, 10, 3)),
        k=2,
        hn_final_points={"https://news.ycombinator.com/item?id=1": 500, "https://a.com/9": 10},
    )
    by_title = {r.title.split(":")[0]: r for r in rows}
    assert by_title["A"].n_domains_14d == 3 and by_title["A"].important_cov
    assert not by_title["B"].important_cov and by_title["C"].important_cov
    assert by_title["A"].max_hn_points_final == 500
    assert rep.k == 2 and rep.n_stories == 3


def test_calibration_targets_n_per_day():
    rows = [
        hs.LabelRow(
            story_id=f"s{i}",
            first_seen=datetime(2026, 10, 1 + i % 2, tzinfo=UTC),
            title="t",
            urls=[],
            n_domains_14d=i % 6,
            max_hn_points_final=i * 10,
        )
        for i in range(40)
    ]
    k = hs.calibrate_k(rows, target_per_day=4, days=2)
    assert abs(sum(r.n_domains_14d >= k for r in rows) / 2 - 4) <= 3
    cut = hs.calibrate_hn_cutoff(rows, target_per_day=4, days=2)
    assert sum((r.max_hn_points_final or 0) >= cut for r in rows) == 8


def test_label_map_from_urls_and_write(tmp_path):
    rows = [
        hs.LabelRow(
            story_id="hs-1",
            first_seen=datetime(2026, 10, 1, tzinfo=UTC),
            title="t",
            urls=["https://a.com/1", "https://b.com/2"],
            n_domains_14d=2,
            max_hn_points_final=None,
        )
    ]
    m = hs.label_map_from_urls({"st-9": ["https://b.com/2"], "pc-1": ["https://z.com"]}, rows)
    assert m == {"st-9": "hs-1"}
    hs.write_labels(rows, tmp_path / "labels.parquet")
    assert (tmp_path / "labels.jsonl").read_text().count("hs-1") == 1


def test_url_clusterer():
    obs = [
        obs_("x", "https://a.com/1?ref=1", 0),
        obs_("y", "https://a.com/1/", 1),
        obs_("z", "https://b.com/", 0),
    ]
    assert sorted(len(c) for c in hs.UrlClusterer().cluster(obs)) == [1, 2]
