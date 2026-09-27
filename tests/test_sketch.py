"""Phase 7: near-duplicate lines and word rarity."""
from recall.sync.sketch import SeenLines, WordRarity, simhash


def test_rerendered_lines_are_duplicates_but_new_lines_are_not():
    seen = SeenLines()
    line = "Sarah: the vendor shortlist is Acme, Globex and Initech, we decide by Friday afternoon"
    assert not seen.seen(line)
    assert seen.seen(line)
    assert seen.seen(line + " (edited)")  # small re-render
    assert not seen.seen("Message 12: discussing the hotkey fallback and vector search plan, step 12")
    assert not seen.seen("Message 13: discussing the hotkey fallback and vector search plan, step 13")
    assert not seen.seen("Dev: Globex was cheapest but their support contract only covers weekdays")
    assert bin(simhash(line) ^ simhash("completely different words about vector databases")).count("1") > 10


def test_seen_lines_stay_bounded():
    seen = SeenLines(capacity=100)
    for i in range(500):
        seen.seen(f"log line number {i} with request id {i * 7919} finished")
    assert len(seen._hashes) == 100
    assert sum(len(keys) for band in seen._bands for keys in band.values()) == 800


def test_common_words_weigh_less_than_rare_ones(tmp_path):
    rarity = WordRarity()
    for i in range(200):
        rarity.add(f"app text code window snapshot {i}")
    rarity.add("the submarine cable repair crew")
    assert rarity.count("app") == 201 - 1 and rarity.count("submarine") == 1
    assert rarity.idf("submarine") > 3 * rarity.idf("app")
    rarity.decay()
    assert rarity.count("app") == 100
    rarity.save(tmp_path / "rarity.npz")
    assert WordRarity.load(tmp_path / "rarity.npz").count("app") == 100
    assert rarity.counts.nbytes == 128 * 1024
